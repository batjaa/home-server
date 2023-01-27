This is an ongoing draft.

Credits goes to https://github.com/notthebee & https://github.com/geerlingguy.

## Usage

Install Ansible (macOS):
```
brew install ansible
```

Clone the repository:
```
git clone https://github.com/batjaa/home-server
```

Create a host varialbe file and adjust the variables:
```
cd home-server/
mkdir -p host_vars/YOUR_HOSTNAME
vi host_vars/YOUR_HOSTNAME/vars.yml
```

Add your custom inventory file to `hosts`:
```
cp hosts_example hosts
vi hosts
```

Install the dependencies:
```
ansible-galaxy install -r requirements.yml
```

Finally, run the playbook:
```
ansible-playbook main.yml -l your-host-here -K
```
The "-K" parameter is only necessary for the first run, since the playbook configures passwordless sudo for the main login user

For consecutive runs, if you only want to update the Docker containers, you can run the playbook like this:
```
ansible-playbook main.yml --tags="port,containers"
```
