BRC Vendor Work Order System
This is a Flask-based web application for managing work order requests.

Features
User authentication with different roles (Requester, Property Manager, Admin, Scheduler, Super User).

Create, view, edit, and manage work orders.

A dashboard with statistics and charts for tracking requests.

Property and user management for administrators.

Email notifications for key events.

Setup
Create a virtual environment: python -m venv venv

Activate it: source venv/bin/activate (or venv\Scripts\activate on Windows)

Install dependencies: pip install -r requirements.txt

Create a .env file and add your configuration (see .env.example).

Run the application: flask run