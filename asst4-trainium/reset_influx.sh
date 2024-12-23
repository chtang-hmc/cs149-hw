#!/bin/bash

if [ -f influxdata-archive_compat.key ]; then
    rm influxdata-archive_compat.key
fi
sudo dpkg --purge influxdb2
sudo dpkg --purge influxdb2-cli
sudo rm -rf /etc/default/influxdb2
sudo rm -rf /var/cache/apt/archives/influxdb2_2.7.3-1_amd64.deb
sudo rm -rf /var/cache/apt/archives/influxdb2-cli_2.7.3-1_amd64.deb
sudo rm -rf ~/.influxdbv2/
sudo rm -rf /var/lib/influxdb/
