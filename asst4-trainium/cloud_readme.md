# AWS Setup Instructions #

For performance testing, you will need to run this assignment on a VM instance on Amazon Web Services (AWS). We'll be providing (or have already sent) you student coupons that you can use for billing purposes. Here are the steps for how to get setup for running on AWS.

> [!NOTE]
> Please don't forget to SHUT DOWN your instances when you're done for the day to avoid burning through credits overnight!

## Creating a VM ##

1. Log in to the [AWS EC2 dashboard](https://us-east-2.console.aws.amazon.com/console/home?region=us-east-2). On the top right of the page, make sure you are on the `us-east-2` region.
<p align="center">
  <img src="handout/switch-region.png" alt="Switch region" width="25%">
</p>

2. Now you're ready to create a VM instance. Click on the button that says `Launch instance`.

<p align="center">
  <img src="handout/launch-instance.png?raw=true" alt="Launch instance" width="25%">
</p>

3. Click on `Browse more AMIs` AMI.

<p align="center">
  <img src="handout/search-ami.png" alt="Search AMI" width="60%">
</p>

4. Search for `cs149_neuron_v2` in `My AMIs` and make sure to click on `Shared with me`. Click `Select`.

<p align="center">
  <img src="handout/select-ami.png" alt="Select AMI" width="100%">
</p>


5. Choose the `trn1.2xlarge` instance type.

<p align="center">
  <img src="handout/choose-instance.png" alt="Choose instance type" width="80%">
</p>

6. Change the size of the volume to 150 GB to accomodate the packages we will need to install to make the instance functional for the assignment:

<p align="center">
  <img src="handout/storage.png?raw=true" alt="Storage" width="70%">
</p>

7. You will need a key pair to access your instance. In `Key pair (login)` section, click `Create a new key pair` and give it whatever name you'd like. This will download a keyfile to your computer called `<key_name>.pem` which you will use to login to the VM instance you are about to create. Finally, you can launch your instance.

<p align="center">
  <img src="handout/keypair-step1.png" alt="Key Pair Step 1" width="70%">
  <img src="handout/keypair-step2.png" alt="Key Pair Step 2" width="50%">
</p>

8. Confirm all details and launch instance  

<p align="center">
  <img src="handout/confirm-launch.png" alt="Confirm" width="35%">
</p>

9. Now that you've created your VM, you should be able to __SSH__ into it. You need the public IPv4 DNS name to SSH into it, which you can find by navigating to your instance's page and then clicking the `Connect` button, followed by selecting the SSH tab (note, it may take a moment for the instance to startup and be assigned an IP address):

<p align="center">
  <img src="handout/connect.png?raw=true" alt="Connect" width="90%">
</p>

Make sure you follow the instructions to change the permissions of your key file by running `chmod 400 path/to/key_name.pem`.
Once you have the IP address, you can login to the instance. We are going to be using `neuron-profile` in this assignment, which uses InfluxDB to store profiler metrics. As a result, you will need to forward ports 3001 (the default neuron-profile HTTP server port) and 8086 (the default InfluxDB port) in order to view `neuron-profile` statistics in your browser. You can login to the instance while also forwarding the needed ports by running this command:
~~~~
ssh -i path/to/key_name.pem ubuntu@<public_dns_name> -L 3001:localhost:3001 -L 8086:localhost:8086
~~~~

> [!NOTE]
> Make sure you login as the user "ubuntu" rather than the user "root".

> [!WARNING]
> If you need to step away during setup after creating your instance, be sure to shut it down. Leaving it running could deplete your credits, and you may incur additional costs.


## Fetching your code from AWS ##

We recommend that you create a private git repository and develop your assignment in there. It reduces the risk of losing your code and helps you keep track of old versions.

Alternatively, you can also use `scp` command like following in your local machine to fetch code from a remote machine.
~~~~
scp -i <path-to-your-pem-file> ubuntu@<instance-IP-addr>:/path/to/file /path/to/local_file
~~~~

## Shutting down VM ##
When you're done using the VM, you can shut it down by clicking "stop computer" in the web page, or using the command below in the terminal.
~~~~
sudo shutdown -h now
~~~~
