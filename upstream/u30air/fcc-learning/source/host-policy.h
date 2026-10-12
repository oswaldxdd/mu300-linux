/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef U30_HOST_POLICY_H
#define U30_HOST_POLICY_H
/* One tick = two seconds. Pure policy, also exercised by host-side C tests. */
enum u30_mode { U30_AUTO, U30_DEVICE, U30_HOST };
enum u30_action { U30_KEEP, U30_TO_DEVICE, U30_TO_HOST };
struct u30_policy {
 enum u30_mode mode;
 unsigned int stable, missing_nic, disconnected, retry_wait;
 int blocked;
};
struct u30_input {
 int readable, powered, boost, host, gadget_busy, nic, ready;
};
static inline enum u30_action u30_tick(struct u30_policy *p, struct u30_input i)
{
 if (!i.readable || !i.powered || i.boost) {
  p->stable = p->missing_nic = p->retry_wait = 0;
  /* An I2C failure is not evidence of physical disconnection. */
  if (i.readable && !i.powered && !i.boost) {
   if (p->disconnected < 3) p->disconnected++;
   if (p->disconnected == 3) p->blocked = 0;
  } else p->disconnected = 0;
  return i.host ? U30_TO_DEVICE : U30_KEEP;
 }
 p->disconnected = 0;
 if (p->mode == U30_DEVICE) {
  p->stable = p->missing_nic = p->retry_wait = 0;
  return i.host ? U30_TO_DEVICE : U30_KEEP;
 }
 if (i.host) {
  p->stable = p->retry_wait = 0;
  if (p->mode == U30_AUTO && !i.nic) {
   if (p->missing_nic < 8) p->missing_nic++;
   if (p->missing_nic == 8) {
    p->blocked = 1;
    return U30_TO_DEVICE;
   }
  } else p->missing_nic = 0;
  return U30_KEEP;
 }
 p->missing_nic = 0;
 if (!i.ready || i.gadget_busy) {
  p->stable = p->retry_wait = 0;
  return U30_KEEP;
 }
 /* A data-only unplug cannot drop charger VBUS. Retry after 60 idle seconds,
  * then the normal 10-second debounce. Never interrupt a PC gadget session.
  */
 if (p->blocked) {
  p->stable = 0;
  if (p->mode == U30_AUTO) {
   if (p->retry_wait < 30) p->retry_wait++;
   if (p->retry_wait == 30) {
    p->blocked = 0;
    p->retry_wait = 0;
   }
  }
  return U30_KEEP;
 }
 if (p->stable < 5) p->stable++;
 if (p->stable == 5) {
  p->stable = 0;
  return U30_TO_HOST;
 }
 return U30_KEEP;
}
#endif
