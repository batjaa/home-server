#!/bin/sh
set -eu
# Docker's FORWARD policy otherwise drops libvirt guests' outbound traffic.
# Libvirt's own NAT rules still control inbound connections to the guest.
for rule in '-i virbr-tn -j ACCEPT' '-o virbr-tn -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT'; do
    # These are constant rule words, intentionally expanded as arguments.
    iptables -C DOCKER-USER $rule 2>/dev/null || iptables -I DOCKER-USER 1 $rule
done
# Coolify's control-plane network may SSH to this team's guest.
# Libvirt can prepend its reject rule after a network restart. Presence alone
# is insufficient: this narrow allowance must precede that rejection.
rule='-s 10.0.1.0/24 -d 192.168.124.10/32 -o virbr-tn -p tcp -m tcp --dport 22 -j ACCEPT'
first=$(iptables -S LIBVIRT_FWI | sed -n '2p')
if [ "$first" != "-A LIBVIRT_FWI $rule" ]; then
    while iptables -C LIBVIRT_FWI $rule 2>/dev/null; do
        iptables -w -D LIBVIRT_FWI $rule
    done
    iptables -w -I LIBVIRT_FWI 1 $rule
fi
