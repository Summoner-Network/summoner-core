#!/bin/bash

# List of common router IPs in the most common ranges
router_ips=("192.168.1.1" "192.168.0.1" "192.168.100.1" "192.168.10.1" 
            "10.0.0.1" "172.16.1.1" "10.1.1.1" "192.168.2.1" 
            "192.168.1.254" "192.168.0.254")

# Function to open the router page in the default browser
open_router_page() {
    local router_ip="$1"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        open "http://$router_ip"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        xdg-open "http://$router_ip"
    else
        echo "Unsupported OS"
        exit 1
    fi
}

# Function to discover router IP address
discover_router_ip() {
    # For macOS
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # Use netstat to get the default gateway (router) and filter only the IPv4 address
        router_ip=$(netstat -nr | grep 'default' | awk '{print $2}' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$')
    # For Linux
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Use ip route to get the default gateway (router)
        router_ip=$(ip route | grep default | awk '{print $3}')
    else
        echo "Unsupported OS"
        exit 1
    fi

    # If a valid router IP is found, print it, otherwise exit with an error
    if [[ -z "$router_ip" ]]; then
        echo "Error: Could not detect router IP."
        exit 1
    else
        echo "Found router IP: $router_ip"
    fi
}

# First, try discovering the router IP automatically
discover_router_ip

# If the automatic discovery doesn't work, try the common list of router IPs
if [[ -z "$router_ip" ]]; then
    for ip in "${router_ips[@]}"; do
        echo "Trying router IP: $ip..."
        open_router_page "$ip"
        sleep 2  # Wait for 2 seconds to give the browser time to open the page
    done
else
    # If the automatic discovery works, open it directly
    open_router_page "$router_ip"
fi
