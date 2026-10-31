/* SPDX-License-Identifier: MIT */
#ifndef PIX_USB_SERIAL_H
#define PIX_USB_SERIAL_H
#include "usb_selector.h"
#include <stddef.h>
struct libusb_device;
#ifdef __cplusplus
extern "C" {
#endif
/* Internal shared reader for discovery and device selection. No interface claim. */
usb_serial_state usb_device_read_serial(struct libusb_device *, char *, size_t, size_t *);
#ifdef __cplusplus
}
#endif
#endif
