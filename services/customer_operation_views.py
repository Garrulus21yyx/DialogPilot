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


def refund_lookup_read_view(observation):
    """Project a scoped lookup without interpreting absence as payment outcome."""
    if observation.request is not None:
        data = observation.request.to_dict()
        data.pop('user_id', None)
        data['lookup_status'] = 'FOUND'
    else:
        data = {'order_id': observation.order_id, 'lookup_status': 'NO_APPLICATION',
                'order_version': observation.order_version}
    data['field_semantics'] = {
        'lookup_status': 'FOUND 表示找到归属于当前用户的退款申请。NO_APPLICATION 仅用于普通订单查询，表示有权访问的订单在本系统当前未记录退款申请，不证明外部退款不存在、退款未到账或用户无权申请退款。',
    }
    return data


class UnsupportedRefundObservation(ValueError):
    """The captured refund observation does not satisfy the display contract."""


def refund_lookup_statements(data, *, locale="zh-CN"):
    """Render the supported refund observation; no eligibility or payment inference.

    Called only for an authoritative refund.current_state fact. Caller-side
    projections must not infer this fact type from similarly named fields.
    """
    if locale not in {"zh-CN", "en"}:
        raise UnsupportedRefundObservation('unsupported refund display locale')
    if not isinstance(data, dict):
        raise UnsupportedRefundObservation('refund observation must be an object')
    order_id = data.get('order_id')
    if not isinstance(order_id, str) or not order_id.strip():
        raise UnsupportedRefundObservation('refund observation requires order identity')
    if data.get('lookup_status') == 'NO_APPLICATION':
        if type(data.get('order_version')) is not int or data['order_version'] < 1:
            raise UnsupportedRefundObservation('absence requires observed order version')
        if any(k in data for k in ('refund_id', 'status')):
            raise UnsupportedRefundObservation('absence cannot contain an application state')
        return (
            ('lookup', f'No refund application belonging to you is currently recorded for order {order_id} in this system.'
             if locale == 'en' else f'订单 {order_id} 在本系统当前未记录到归属于您的退款申请。'),
            ('arrival', 'The current system record does not establish whether a refund has reached your account.'
             if locale == 'en' else '无法从当前系统记录确认退款是否已经到账。'),
        )
    if data.get('lookup_status') == 'FOUND':
        labels = {'requested': '已申请', 'reviewing': '审核中', 'approved': '已批准',
                  'rejected': '已拒绝', 'refunded': '已退款'}
        refund_id, status = data.get('refund_id'), data.get('status')
        if not isinstance(refund_id, str) or not refund_id.strip() or status not in labels:
            raise UnsupportedRefundObservation('found refund requires known application identity and state')
        return (
            ('lookup', f'The recorded status of refund application {refund_id} for order {order_id} is "{status}".'
             if locale == 'en' else f'订单 {order_id} 的退款申请 {refund_id} 在本系统的记录状态为“{labels[status]}”。'),
        )
    raise UnsupportedRefundObservation('unsupported refund observation state')
