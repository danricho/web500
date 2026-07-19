#!/bin/sh
# RUN THE SERVER EXACTLY AS THE SYSTEMD UNIT DOES (SEE README - ONE WORKER ONLY)
venv/bin/gunicorn -b :4030 -w 1 --threads 100 main:app
