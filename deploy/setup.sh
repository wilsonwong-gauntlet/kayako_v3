#!/bin/bash

# Update system
sudo dnf update -y

# Enable EPEL repository
sudo dnf install -y epel-release

# Install Python and dependencies
sudo dnf install -y python3-pip python3-devel nginx postgresql postgresql-server postgresql-contrib

# Install system dependencies
sudo dnf install -y gcc openssl-devel bzip2-devel libffi-devel

# Initialize PostgreSQL
sudo postgresql-setup --initdb

# Start and enable PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create a PostgreSQL database and user
sudo -u postgres psql << EOF
CREATE DATABASE speech_assistant;
CREATE USER speech_user WITH PASSWORD 'your_password_here';
GRANT ALL PRIVILEGES ON DATABASE speech_assistant TO speech_user;
\q
EOF

# Install Python packages
pip3 install --user -r requirements.txt

# Setup Nginx
sudo bash -c 'cat > /etc/nginx/conf.d/speech-assistant.conf << EOF
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

# Start and enable Nginx
sudo systemctl start nginx
sudo systemctl enable nginx

# Setup systemd service
sudo bash -c 'cat > /etc/systemd/system/speech-assistant.service << EOF
[Unit]
Description=Speech Assistant API
After=network.target

[Service]
User=ec2-user
WorkingDirectory=/home/ec2-user/speech-assistant
Environment="PATH=/home/ec2-user/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/home/ec2-user/.local/bin/uvicorn main:app --host 0.0.0.0 --port 5050
Restart=always

[Install]
WantedBy=multi-user.target
EOF'

# Reload systemd
sudo systemctl daemon-reload

# Start and enable the service
sudo systemctl start speech-assistant
sudo systemctl enable speech-assistant

# Configure PostgreSQL to accept connections
sudo sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/" /var/lib/pgsql/data/postgresql.conf
sudo sed -i "s/#port = 5432/port = 5432/" /var/lib/pgsql/data/postgresql.conf

# Add this line to pg_hba.conf for local access
echo "host    all             all             127.0.0.1/32            md5" | sudo tee -a /var/lib/pgsql/data/pg_hba.conf

# Restart PostgreSQL to apply changes
sudo systemctl restart postgresql 