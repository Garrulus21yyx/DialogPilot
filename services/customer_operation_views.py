"""Business-owned semantics for customer-facing read evidence."""

ORDER_STATUS_LABELS = {
    'paid': '已支付', 'shipped': '已发货', 'delivered': '已送达', 'cancelled': '已取消',
}


def order_read_view(record):
    """Project an internal order snapshot without inventing business event times."""
    data = dict(record)
    data.pop('user_id', None)
    data['record_created_at'] = data.pop('created_at')
    data['status_label'] = ORDER_STATUS_LABELS[data['status']]
    data['field_semantics'] = {
        'status': '订单快照状态；已送达不证明收件人签名或承运方签收事件。',
        'record_created_at': '本系统首次写入此订单记录的时间；不是实际下单时间。',
        'updated_at': '本系统最近更新此订单记录的时间；不是发货、送达或签收事件时间。',
        'refundable_until': '业务退款提交窗口截止时间；缺失表示窗口数据未知，不表示已经过期。',
    }
    return data


def refund_eligibility_read_view(record):
    """Explain an operational submission precheck, distinct from policy entitlement."""
    data = dict(record)
    reason = data['reason_code']
    meanings = {
        'eligible': '满足当前退款提交前置条件。',
        'refund_already_requested': '已有退款申请，本次不能重复提交。',
        'refund_window_missing': '缺少退款窗口数据，当前不能提交；不能据此断言已过期或无权退款。',
        'refund_window_expired': '已知退款提交窗口已过期。',
        **{'order_status_' + status: '订单当前状态为' + label + '，不满足此退款提交前置条件。'
           for status, label in ORDER_STATUS_LABELS.items()},
    }
    data['reason_description'] = meanings[reason]
    data['assessment_scope'] = '退款操作提交前置检查；不评估赠品、商品例外或退款金额政策，政策问题需知识证据。'
    data['assessment_status'] = ('INSUFFICIENT_DATA' if reason == 'refund_window_missing' else
                                 'ELIGIBLE' if data['eligible'] else 'INELIGIBLE')
    data['field_semantics'] = {
        'eligible': '当前是否允许提交退款操作，不是完整售后政策权利判断。',
        'amount_minor': '订单金额，单位为货币最小单位；不是已批准或已到账退款金额。',
        'refundable_until': '提交窗口截止时间，null 表示未知而非过期。',
    }
    return data
