/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdio.h>
#include "host-policy.h"
int main(void)
{
 struct u30_policy p = { .mode = U30_AUTO };
 struct u30_input i = {.readable=1, .powered=1, .ready=1};
 for (int n=0;n<4;n++) assert(u30_tick(&p,i)==U30_KEEP);
 assert(u30_tick(&p,i)==U30_TO_HOST);
 i.host=1; i.nic=1;
 for (int n=0;n<100;n++) assert(u30_tick(&p,i)==U30_KEEP);
 i.powered=0; assert(u30_tick(&p,i)==U30_TO_DEVICE);
 i.host=0; i.powered=1; i.gadget_busy=1;
 for (int n=0;n<100;n++) assert(u30_tick(&p,i)==U30_KEEP);
 i.gadget_busy=0; i.host=1; i.nic=0;
 for (int n=0;n<7;n++) assert(u30_tick(&p,i)==U30_KEEP);
 assert(u30_tick(&p,i)==U30_TO_DEVICE); assert(p.blocked);
 i.host=0;
 for (int n=0;n<29;n++) assert(u30_tick(&p,i)==U30_KEEP);
 assert(p.blocked);
 assert(u30_tick(&p,i)==U30_KEEP); assert(!p.blocked);
 for (int n=0;n<4;n++) assert(u30_tick(&p,i)==U30_KEEP);
 assert(u30_tick(&p,i)==U30_TO_HOST);
 i.host=1; i.nic=1;
 for (int n=0;n<100;n++) assert(u30_tick(&p,i)==U30_KEEP);
 i.nic=0;
 for (int n=0;n<7;n++) assert(u30_tick(&p,i)==U30_KEEP);
 assert(u30_tick(&p,i)==U30_TO_DEVICE); assert(p.blocked);
 i.host=0; i.gadget_busy=1;
 for (int n=0;n<100;n++) assert(u30_tick(&p,i)==U30_KEEP);
 assert(p.blocked && !p.retry_wait);
 i.gadget_busy=0;
 for (int n=0;n<29;n++) assert(u30_tick(&p,i)==U30_KEEP);
 i.readable=0;
 for (int n=0;n<10;n++) assert(u30_tick(&p,i)==U30_KEEP);
 assert(p.blocked);
 i.readable=1; i.powered=0;
 for (int n=0;n<3;n++) assert(u30_tick(&p,i)==U30_KEEP);
 assert(!p.blocked);
 i.powered=1;
 for (int n=0;n<4;n++) assert(u30_tick(&p,i)==U30_KEEP);
 assert(u30_tick(&p,i)==U30_TO_HOST);
 i.host=1; i.boost=1; assert(u30_tick(&p,i)==U30_TO_DEVICE);
 i.boost=0; i.readable=0; assert(u30_tick(&p,i)==U30_TO_DEVICE);
 i.readable=1; p.mode=U30_DEVICE; assert(u30_tick(&p,i)==U30_TO_DEVICE);
 i.host=0;
 for (int n=0;n<100;n++) assert(u30_tick(&p,i)==U30_KEEP);
 p.mode=U30_AUTO; i.ready=0;
 for (int n=0;n<100;n++) assert(u30_tick(&p,i)==U30_KEEP);
 p.mode=U30_HOST; i.host=1;
 for (int n=0;n<100;n++) assert(u30_tick(&p,i)==U30_KEEP);
 puts("PASS: debounce, power loss, I2C failure, boost fault, PC priority, NIC timeout, powered hotplug retry, reconnect, manual Device, boot delay, manual Host");
 return 0;
}
