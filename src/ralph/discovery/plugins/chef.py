#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Discovery configuration."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import datetime
import random
import re
import time

from lck.django.common import nested_commit_on_success
import MySQLdb
from django.conf import settings

from ralph.util import network, plugin, Eth
from ralph.discovery.models import (IPAddress, DiskShare, DiskShareMount, DeviceType, Device, OperatingSystem,
    ComponentModel, ComponentType, Storage, SERIAL_BLACKLIST,
    DISK_VENDOR_BLACKLIST, DISK_PRODUCT_BLACKLIST)
from ralph.discovery import hardware

import chef

SAVE_PRIORITY = 52

API_URL = settings.CHEF_API_URL
API_KEY = settings.CHEF_API_KEY
API_USER = settings.CHEF_API_USER

def get_ip_hostname_sets(ip):
    hostname_set = {network.hostname(ip)}
    try:
        ip_address = IPAddress.objects.get(address=ip)
        if ip_address.device:
            ip_set = set()
            for ip in ip_address.device.ipaddress_set.all():
                ip_set.add(ip.address)
                if ip.hostname:
                    hostname_set.add(ip.hostname)
        else:
            ip_set = {ip}
    except IPAddress.DoesNotExist:
        ip_set = {ip}
    return ip_set, hostname_set

@plugin.register(chain='discovery', requires=['ping'], priority=200)
def cheff(**kwargs):
    ip = str(kwargs['ip'])
    chef_api = chef.ChefAPI(API_URL, API_KEY, API_USER)
    search = chef.Search('node', q="ipaddress:%s"%ip)
    node_data = search.data['rows'][0]
    dev, dev_name = parse_attributes(node_data)
    ip_address, created = IPAddress.concurrent_get_or_create(address=str(ip))
    ip_address.device, message = dev, dev_name
    if created:
        ip_address.hostname = network.hostname(ip_address.address)
    ip_address.last_puppet = datetime.datetime.now()
    ip_address.save(update_last_seen=True) # no priorities for IP addresses

    return True, message, kwargs

def is_host_virtual(node_data):
    is_virtual=False
    if node_data['automatic']['virtualization']['role'] == 'guest':
        is_virtual = True
    return is_virtual
        

def parse_attributes(node_data):
    sn = node_data['automatic']['dmi']['base_board']['serial_number']
    if sn in SERIAL_BLACKLIST:
        sn = None
    prod_name = node_data['automatic']['dmi']['base_board']['product_name']
    manufacturer = node_data['automatic']['dmi']['base_board']['manufacturer']
    model_name = "{} {}".format(manufacturer, prod_name)
    dev_name = model_name
    if DeviceType.blade_server.matches(model_name):
        model_type = DeviceType.blade_server
    else:
        model_type = DeviceType.rack_server
    ip_addresses, ethernets = handle_data_ethernets(node_data)
    dev = Device.create(sn=sn, model_name=model_name, model_type=model_type,
                        ethernets=ethernets, priority=SAVE_PRIORITY)
    
    return dev, dev_name

def handle_data_ethernets(node_data):
    ethernets = []
    ip_addresses = []
    for interface in node_data['automatic']['network']['interfaces']:
        for address in node_data['automatic']['network']['interfaces'][interface]['addresses']:
            if node_data['automatic']['network']['interfaces'][interface]['addresses'][address]['family'] != 'lladdr':
	        try:
                    ip = network.validate_ip(address.format(interface))
                    ip_addresses.append(ip)
                except (ValueError, KeyError):
                    pass
            else:
                mac = address.format(interface)
                label = 'Ethernet {}'.format(interface)
                ethernets.append(Eth(label, mac, speed=None))
    return ip_addresses, ethernets           
