# Home Assistant Add-on: AppKettle

## How to use

Firstly you need to force the kettle into local operating mode, this is done by blocking its internet access.  How you do this is up to you and will be dependent on your network setup and abilities. Possible options are to restrict the kettle to a VLAN without internet access, to add a static DHCP reservation and block the its IP on your firewall or modify the DHCP options so that no default gateway is issued to the kettle.  You could also redirect DNS queries of jingxuncloud.com (the domain the kettle tries to access) to a dummy address.

Once done, add the repo (https://github.com/longmover/ha_addons) to Home Assistant in the Addons section, refresh and install the addon

Configure your MQTT server variables, save and start the addon.  All being well the addon will discover your kettle and you should see it appear automatically in HA.  The sensors and controls are self explanatory.

## Calibrating the fill level sensor

The fill level is measured by weight but the addon will convert this to % however you may need to calibrate it to match your specific kettle

To do this first empty the kettle and put it on the base, make a note of the "Kettle Water Volume" value.  Next fill the kettle to its maximum level and record the value.

Once you have the min and max level values you can stop the addon, update the values in the configuration and start it again.  It should now be accurate.

I hope this is of use to someone out there, the code is based on the exellect work by https://github.com/tinaught/
