d:\Projekte\Code\jaeronautics\.venv\Scripts\pybabel.exe extract -F babel.cfg -o messages.pot .

d:\Projekte\Code\jaeronautics\.venv\Scripts\pybabel.exe update -i messages.pot -d translations -D messages

translate strings inside messages.po

save the file and check if the dates match:
dir translations\de\LC_MESSAGES\messages.*

d:\Projekte\Code\jaeronautics\.venv\Scripts\pybabel.exe compile -d translations -D messages -f



resend emails:

export FLASK_APP=/var/www/aeronautics-members/app.py
/var/www/aeronautics-members/.venv/bin/flask send-welcome-email "angelo.popovic29@gmail.com"




Flask configuration
SECRET_KEY='YOUR_GENERATED_SECURE_KEY_HERE'
LANGUAGES='en,de'

Database configuration (for mysql+pymysql)
DB_HOST=192.168.1.98
DB_NAME=jaeronautics
DB_USER=jaeronautics
DB_PASSWORD=YnV3Q6T34uTfzSdA
DB_PORT=3306


