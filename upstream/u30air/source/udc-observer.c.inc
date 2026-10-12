/* U30 Air local API: inspect gadget state without exposing UDC internals. */
int usb_u30air_gadget_busy(void)
{
	struct usb_udc *udc;
	unsigned long flags;
	int busy = -ENODEV;

	mutex_lock(&udc_lock);
	list_for_each_entry(udc, &udc_list, list) {
		enum usb_device_state state;

		spin_lock_irqsave(&udc->gadget->state_lock, flags);
		state = udc->gadget->state;
		spin_unlock_irqrestore(&udc->gadget->state_lock, flags);
		if (state == USB_STATE_DEFAULT || state == USB_STATE_ADDRESS ||
		    state == USB_STATE_CONFIGURED || state == USB_STATE_SUSPENDED) {
			busy = 1;
			break;
		}
		busy = 0;
	}
	mutex_unlock(&udc_lock);
	return busy;
}
EXPORT_SYMBOL_GPL(usb_u30air_gadget_busy);
