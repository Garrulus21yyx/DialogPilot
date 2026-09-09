"""Public result views for the retail tool API, independent of benchmark tasks."""
from application.capability_registry import ResultField


ADDRESS = tuple(ResultField(('address', key), zh, en) for key, zh, en in (
    ('address1', '街道', 'Street'), ('address2', '补充地址', 'Address line 2'),
    ('city', '城市', 'City'), ('state', '州', 'State'), ('zip', '邮编', 'Postal code'),
    ('country', '国家', 'Country')))
ORDER = ResultField(('order_id',), '订单', 'Order')
STATUS = ResultField(('status',), '记录状态', 'Recorded status')

FIELDS = {
    'modify_user_address': ADDRESS,
    'modify_pending_order_address': (ORDER, STATUS, *ADDRESS),
    'exchange_delivered_order_items': (ORDER, STATUS,
        ResultField(('exchange_price_difference',), '换货差额（不代表到账）',
                    'Exchange difference (not settlement)', 'charge_delta', 'USD')),
    'cancel_pending_order': (ORDER, STATUS),
    'return_delivered_order_items': (ORDER, STATUS),
}
