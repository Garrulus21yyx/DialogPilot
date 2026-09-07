"""Synthetic development policies with contradictory applicability and versions.

These rules are invented solely for engineering evaluation, not real store policy.
"""
from evaluation.rag_pipeline.contracts import RagDocument, RagCase, EvidenceSpan


def applicability_catalog():
    """The synthetic host owns the channel vocabulary used by its policies."""
    from application.sales_channels import filter_contract
    return filter_contract({'catalog_id':'synthetic-applicability-v1',
                            'sales_channels':{'web':'模拟商店官网购买渠道','store':'模拟商店线下门店购买渠道'}})


def applicability_development():
    docs, cases, options, forbidden = [], [], {}, {}

    def document(key, title, text, **metadata):
        docs.append(RagDocument(key, title, text, {'effective_from':'2020-01-01T00:00:00+00:00', **metadata}))
        return docs[-1]

    def case(key, query, document, quote=None, exclude=(), **scope):
        quote = quote or document.content
        start = document.content.index(quote)
        cases.append(RagCase('scope:'+key, 'scope:'+key, 'dev', query, evidence=(EvidenceSpan(document.document_id,start,start+len(quote),quote),)))
        options[cases[-1].case_id] = {'as_of':'2026-06-01T00:00:00+00:00', **scope}
        forbidden[cases[-1].case_id] = list(exclude)

    cn = document('returns-cn','无理由退货期限','中国区未拆封普通商品的无理由退货申请期限为签收后七日。',region='CN')
    eu = document('returns-eu','无理由退货期限','欧洲区未拆封普通商品的无理由退货申请期限为签收后十四日。',region='EU')
    general = document('returns-general','退货计时说明','退货期限以签收记录为起点。是否符合条件还需核实商品类别和状态。')
    case('cn','中国区未拆封普通商品签收后几天内可以申请无理由退货？',cn,exclude=('returns-eu',),applicable_region='CN')
    case('eu','欧洲区未拆封普通商品的无理由退货申请期限是多少？',eu,exclude=('returns-cn',),applicable_region='EU')
    case('global','中国区计算退货期限从哪个记录开始？',general,exclude=('returns-eu',),applicable_region='CN')
    web = document('coupon-web','优惠券恢复规则','官网渠道取消未发货的整单后，未过期优惠券可以恢复一次。',channel='web')
    shop = document('coupon-store','优惠券恢复规则','门店渠道取消未发货的整单后，已使用优惠券不恢复。',channel='store')
    case('web','官网取消未发货整单，未过期优惠券恢复吗？',web,exclude=('coupon-store',),sales_channel='web')
    case('store','门店取消未发货整单，优惠券恢复吗？',shop,exclude=('coupon-web',),sales_channel='store')
    m10 = document('battery-m10','替换电池说明','设备M10只能使用B10替换电池，禁止安装B20。',product='M10')
    m20 = document('battery-m20','替换电池说明','设备M20应使用B20替换电池，B10不适配。',product='M20')
    case('m10','M10能安装B20替换电池吗？',m10,exclude=('battery-m20',),applicable_product='M10')
    case('m20','M20使用B20还是B10替换电池？',m20,exclude=('battery-m10',),applicable_product='M20')
    old = document('return-shipping','退货运费报销','普通质量退货的标准寄回运费报销上限为十二元。',effective_to='2026-04-01T00:00:00+00:00')
    new = document('return-shipping','退货运费报销','普通质量退货的标准寄回运费报销上限为十八元。',effective_from='2026-04-01T00:00:00+00:00')
    case('old','2026年3月购买时，质量退货标准寄回运费最多报销多少？',old,as_of='2026-03-01T00:00:00+00:00')
    case('new','2026年5月购买时，质量退货标准寄回运费最多报销多少？',new,as_of='2026-05-01T00:00:00+00:00')
    removed = document('cashback-withdrawn','取消订单返现','取消订单即可获得五十元返现。')
    active = document('cashback-current','取消订单规则','取消订单按原支付渠道退回实际付款，不额外发放返现。')
    case('withdrawn','取消订单除了退回付款，还有额外返现吗？',active,exclude=(removed.document_id,))
    sections = [
        '资料提交：售后申请应提供订单号、商品现状照片和问题描述。客服核对资料完整性后告知补交事项，资料接收不代表最终审核已经通过。',
        '签收核实：运输记录只用于确定包裹交付情况，不用于推定商品没有瑕疵。出现代收记录时，应补充实际收货人与领取时间，不能跳过商品情况核实。',
        '外观检查：申请人应拍摄包装与商品的不同角度，保留配件照片。仅凭包装变形不能确定内部商品发生损坏，还需要查看具体商品并记录现状。',
        '型号核对：同一系列的不同型号可能使用不同配件，应以铭牌和订单记载核对。相似外观不能代替型号确认，不能建议用户尝试不确定兼容性的组件。',
        '仓库安排：退货地址由售后确认后提供，仓库根据商品类别安排收货。商品包装上的生产商地址不等于本次退货地址，申请人应等待明确的寄回指引。',
        '运输包装：寄回时应保护易损部位并清点配件。物流面单用于追踪包裹，不能作为退款到账证明；仓库收到包裹后仍需核对申请资料与商品情况。',
        '费用核对：实际支付金额与商品标价可能不同，优惠券分摊应以订单记录为准。退款金额不能直接按标价推算，活动赠品还需要按对应活动规则核实。',
        '检测安排：需要检测的商品应记录接收时间和检测项目。检测完成后会形成结果，排队等待检测不等于认定故障，也不等于已经批准免费更换。',
        '退款处理：审核通过后由支付渠道处理款项，提交退款请求与资金到账是不同阶段。用户询问某笔款项是否到账时，应查询相应业务记录。',
        '换货安排：换货审核与库存核对分别进行，符合换货条件不等于目标型号有货。暂时缺货时应说明情况并协商等待安排，不能直接承诺立即发出。',
        '工单沟通：客服会保留已确认事实以及尚待补充的资料。不同客服接手时应查看工单状态，避免把历史处理建议当成本次已执行结果。',
        '时间说明：预计处理时间用于说明通常流程，不构成指定日期完成的保证。节假日或额外核实时应说明影响因素，并以实际业务进展更新用户。',
        '通用退货条件：未拆封普通商品在签收后七日内可申请无理由退货，配件与赠品应保持齐全。',
        '重要例外：刻字定制商品不适用上述无理由退货，即使未拆封且签收不足七日也不适用；质量问题仍可申请专项审核。',
    ]
    long = document('long-after-sales','综合售后手册','\n\n'.join(sections),region='CN')
    case('long-exception','刻字定制商品未拆封、签收三天，能按七日无理由退吗？',long,quote=sections[-1])
    case('long-address','综合售后手册中，寄回地址和退款到账分别怎样确认？',long,quote=sections[4])
    c=cases[-1]; second=sections[8]; start=long.content.index(second)
    from dataclasses import replace
    cases[-1]=replace(c,evidence=c.evidence+(EvidenceSpan(long.document_id,start,start+len(second),second),))
    return tuple(docs),tuple(cases),options,forbidden,(removed.document_id,)
