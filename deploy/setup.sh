#!/bin/bash

# Update system
sudo apt-get update
sudo apt-get upgrade -y

# Install Python and dependencies
sudo apt-get install -y python3-pip python3-dev nginx postgresql postgresql-contrib

# Install system dependencies
sudo apt-get install -y build-essential libssl-dev libffi-dev

# Create a PostgreSQL database and user
sudo -u postgres psql << EOF
CREATE DATABASE speech_assistant;
CREATE USER speech_user WITH PASSWORD 'your_password_here';
GRANT ALL PRIVILEGES ON DATABASE speech_assistant TO speech_user;
\q
EOF

# Install Python packages
pip3 install -r requirements.txt

# Setup Nginx
sudo bash -c 'cat > /etc/nginx/sites-available/speech-assistant << EOF
server {
    listen 80;
    server_name your_domain.com;

    location / {
        proxy_pass http://localhost:5050;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF'

# Enable the Nginx site
sudo ln -s /etc/nginx/sites-available/speech-assistant /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo systemctl restart nginx

# Setup systemd service
sudo bash -c 'cat > /etc/systemd/system/speech-assistant.service << EOF
[Unit]
Description=Speech Assistant API
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/speech-assistant
Environment="PATH=/home/ubuntu/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/home/ubuntu/.local/bin/uvicorn main:app --host 0.0.0.0 --port 5050
Restart=always

[Install]
WantedBy=multi-user.target
EOF'

# Start and enable the service
sudo systemctl start speech-assistant
sudo systemctl enable speech-assistant 