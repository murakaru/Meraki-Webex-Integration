#!/usr/bin/env python
"""External Captive Portal Web Server."""

"""The provided sample code in this repository will reference this file to get the
information needed to connect to your lab backend.  You provide this info here
once and the scripts in this repository will access it as needed by the lab.
Copyright (c) 2019 Cisco and/or its affiliates.
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import json
import os
import sys
import pymongo
import pystache
import random
import string
from pprint import pprint

import requests 
from flask import Flask, json, redirect, render_template, request, \
                make_response, send_from_directory, url_for 
import webexteamssdk

here = os.path.abspath(os.path.dirname(__file__))
project_root = os.path.abspath(os.path.join(here, ".."))
sys.path.insert(0, project_root)


# Module Variables
BASE_URL = "https://api.meraki.com/api/v0/"
CAPTIVE_PORTAL_URL = "" # Input webhook url
BASE_GRANT_URL = ""
USER_CONTINUE_URL = ""
SUCCESS_URL = ""
GRANT_URLS = []
NETWORK_ID = "" # Input Meraki Network ID
snapshot = ""
MERAKI_API_KEY = "" # Input Meraki API Key
DB_URI = "" # Input DB URL

BOT_TOKEN = "" # Input WebexBot Token 

app = Flask(__name__)

# Insert partner list from CSV file into DB
def insert_partners_db(partners_list):
    # Connect to Mongo DB instance
    client = pymongo.MongoClient(DB_URI)

    db = client.get_default_database()

    partners = db["partners"]

    partners.insert_many(partners_list)


# Read partner list from CSV file
def read_partners(filename):
    # Create a list
    partners_list = []
    #Open file in read mode - Par is a variable to access the file
    with open(filename) as par:
        # Ignore description line
        par.readline()
        for line in par:
            # Remove unneeded lines from CSV file
            partner_infor = line.strip().split(",")

            partners_list.append(
                {
                "name":partner_infor[0].replace("\t","").replace("\"","").strip(),
                "email":partner_infor[1].strip()
                }
            )
    return partners_list

# Get Network ID
def get_network_id(org_name, network_name):
    """Get the network ID for a Meraki Network"""

    # Retrieve the list of organizations
    response = requests.get(
        BASE_URL + "organizations",
        headers={"X-Cisco-Meraki-API-Key": MERAKI_API_KEY}
    )
    response.raise_for_status()

    orgs = response.json()
    pprint(orgs)

    for org in orgs:
        if org["name"] == org_name:
            response = requests.get(
                BASE_URL + "organizations/" + org["id"] + "/networks",
                headers={
                    "X-Cisco-Meraki-API-Key": MERAKI_API_KEY,
                    "Content-Type": "application/json"
                },
            )
            response.raise_for_status()

            # Parse and print the JSON response
            networks = response.json()
            pprint(networks)

            for network in networks:
                if network["name"] == network_name:
                    return network["id"]

#List the splash or RADIUS users configured under Meraki Authentication for a network
def get_meraki_users(network_name):

    response = requests.get(
        BASE_URL + "networks/" + NETWORK_ID + "/merakiAuthUsers",
        headers={
            "X-Cisco-Meraki-API-Key": MERAKI_API_KEY,
            "Content-Type": "application/json"
        },
    )
    response.raise_for_status()

    # Parse and print the JSON response
    merakiAuthUsers = response.json()
    pprint(merakiAuthUsers)

    return merakiAuthUsers

#check email of the user in the meraki partner list
def is_partner_meraki(email, merakiAuthUsers):

    for user in merakiAuthUsers:
        if email == user ["email"]:
            return True

    return False


#check for users in the mongo database
def is_partner(email):

    client = pymongo.MongoClient(DB_URI)

    db = client.get_default_database()

    partners = db["partners"]

    entry = partners.find_one({"email":email})
    pprint(entry)

    if entry:
        return True
    else:
        return False


def get_message(session, payload):
    headers = {
        'content-type': 'application/json',
        'authorization': f'Bearer {BOT_TOKEN}'
    }
    url = f'https://api.ciscospark.com/v1/messages/{payload["data"]["id"]}'
    response = session.get(url, headers=headers)
    return response.json()['text']


def get_card_data(session, payload):
    headers = {
        'content-type': 'application/json',
        'authorization': f'Bearer {BOT_TOKEN}'
    }
    response = session.get(f'https://api.ciscospark.com/v1/attachment/actions/{payload["data"]["id"]}', headers=headers)
    return response.json()

def get_person_info(session, payload):
    headers = {
        'content-type': 'application/json',
        'authorization': f'Bearer {BOT_TOKEN}'
    }
    url = f'https://api.ciscospark.com/v1/people/{payload["personId"]}'
    response = session.get(url, headers=headers)
    return response.json()

#save the guest information in mongo db
def save_guest_db(guest):
    client = pymongo.MongoClient(DB_URI)
    db = client.get_default_database()
    guests = db["guest"]
    guests.insert_one(guest)

#saves the password in mongo db
def save_password_in_db(entry):
    client = pymongo.MongoClient(DB_URI)
    db = client.get_default_database()
    passwords = db["passwords"]
    passwords.update_one({"user_email": entry["user_email"]}, {"$set": {"password": entry["password"]}}, upsert=True)

#check if the password is in the database
def is_password_in_db(password):
    client = pymongo.MongoClient(DB_URI)
    db = client.get_default_database()
    passwords = db["passwords"]
    if passwords.find_one({"password": password}):
        return True
    else:
        return False

#remove password from the database once it is used. This ensures that each user has their own password.
def remove_password_from_db(password):
    client = pymongo.MongoClient(DB_URI)
    db = client.get_default_database()
    passwords = db["passwords"]
    passwords.delete_one({"password": password})


@app.route("/absetobot", methods = ["POST"])
def abseto_handler():
    global GRANT_URLS

    payload = request.get_json()
    pprint(payload)
    # When using DialogFlow, POST payload is embedded in a slightly different
    # JSON structure. Check if key originalDetectIntentRequest is present to know if
    # payload is from DialogFlow or Webex
    if "originalDetectIntentRequest" in payload:
        payload = payload["originalDetectIntentRequest"]["payload"]["data"]
    session = requests.Session()
    teams_api = webexteamssdk.WebexTeamsAPI(access_token=BOT_TOKEN)
    # If message came from DialogFlow this check is redundant, but in
    # order to keep the logic simpler and the code more versatile we check that
    # the resource value is messages
    if payload["resource"] == "messages":
         # filter bot self triggered message
        if payload["data"]["personEmail"] == "absetotest@webex.bot":
            return "OK"

        message = get_message(session, payload)
        pprint(f"Message received: {message}")
        if message.strip().lower() == "wifi":
            with open('templates/message.json') as fp:
                text = fp.read()

            converted = pystache.render(text)
            card = json.loads(converted)
            data = [
                    {'contentType': 'application/vnd.microsoft.card.adaptive', 'content': card}
                ]
            teams_api.messages.create(roomId=payload["data"]["roomId"], text="Intro card", attachments=data)
        else:
            teams_api.messages.create(roomId=payload["data"]["roomId"], text="Unrecognized command")
    elif payload["resource"] == "attachmentActions":
        card_data = get_card_data(session, payload)
        pprint(card_data)

        person_info = get_person_info(session, card_data)
        pprint(person_info)


        #ensure that the user selects both fields in the Card - User Type and location.
        #Returns error if only one field is selected
        if "user_type" not in card_data["inputs"] or "location" not in card_data["inputs"]:

            with open('templates/card_selection_error.json') as fp:
                text = fp.read()

            converted = pystache.render(text,{"display_name":person_info["displayName"],"user_email": person_info["emails"][0]})
            card = json.loads(converted)
            data = [{'contentType': 'application/vnd.microsoft.card.adaptive', 'content': card}]


            teams_api.messages.create(roomId = payload["data"]["roomId"], text = "select both fields", attachments = data)

            return "OK"

        is_partner = is_partner_meraki(person_info["emails"][0],get_meraki_users("CiscoEdgeKZN1"))

        if card_data["inputs"]["user_type"] == "partner":
            if len(person_info["emails"]) and is_partner:

                with open("templates/welcome.json") as fp:
                    text = fp.read()
                #generate random password for each partner
                #this uses an uppercase character, lowercase character and digit
                #each password is 8 characters long.
                password = "".join(random.choices(string.ascii_uppercase + string.ascii_lowercase + string.digits, k=8))
                #save the password in mongodb
                save_password_in_db({"user_email": person_info["emails"][0], "password": password})

                converted = pystache.render(text,{"user_name":person_info["displayName"], "location":card_data["inputs"]["location"],"user_link": CAPTIVE_PORTAL_URL, "password": password})
                card = json.loads(converted)
                data = [
                        {'contentType': 'application/vnd.microsoft.card.adaptive', 'content': card}
                    ]

                teams_api.messages.create(roomId = payload["data"]["roomId"], text = "Welcome card" , attachments = data)
            else:
                with open('templates/no_partner.json') as fp:
                    text = fp.read()

                converted = pystache.render(text,{"display_name":person_info["displayName"], "location":card_data["inputs"]["location"], "user_email": person_info["emails"][0]})
                card = json.loads(converted)
                data = [{'contentType': 'application/vnd.microsoft.card.adaptive', 'content': card}]

                teams_api.messages.create(toPersonEmail="amurakar@cisco.com" , text = "New partner registration Request", attachments = data)

                teams_api.messages.create(roomId = payload["data"]["roomId"], text = "Thank you. The ambassador has been informed. We shall get back to you shortly")

        elif card_data["inputs"]["user_type"] == "guest":
            if is_partner:

                with open('templates/partner_error.json') as fp:
                    text = fp.read()

                converted = pystache.render(text,{"display_name":person_info["displayName"],"location":card_data["inputs"]["location"],"user_email": person_info["emails"][0]})
                card = json.loads(converted)
                data = [{'contentType': 'application/vnd.microsoft.card.adaptive', 'content': card}]

                teams_api.messages.create(roomId = payload["data"]["roomId"], text = "Redirect partner", attachments = data)
            else:
                with open('templates/guest_link.json') as fp:
                    text = fp.read()

                password = "".join(random.choices(string.ascii_uppercase + string.ascii_lowercase + string.digits, k=8))
                save_password_in_db({"user_email": person_info["emails"][0], "password": password})
                converted = pystache.render(text, {"user_name": person_info["displayName"], "location":card_data["inputs"]["location"],"user_link": CAPTIVE_PORTAL_URL, "password": password})
                card = json.loads(converted)
                data = [
                        {'contentType': 'application/vnd.microsoft.card.adaptive', 'content': card}
                    ]

                guest = {"user_name": person_info["displayName"], "user_email": person_info["emails"][0]}
                save_guest_db(guest)

                teams_api.messages.create(roomId = payload["data"]["roomId"], text = "Welcome card" , attachments = data)


    return "OK"


@app.route("/feed/images/<filename>", methods=["GET"])
def get_feed(filename):
    return send_from_directory("feed/images", filename, mimetype="image/jpg")

@app.route("/feed/video/<filename>", methods = ["GET"])
def get_videofeed(filename):
    return send_from_directory("feed/video",filename,mimetype="video/mp4")


@app.route("/favicon.ico")
def get_favicon():
    return send_from_directory("static", "favicon.ico", mimetype="image/png")


@app.route("/")
def get_slash():
    return render_template("index.html")


@app.route("/connect", methods=["GET"])
def connect():
    global BASE_GRANT_URL
    global USER_CONTINUE_URL
    global SUCCESS_URL
    global GRANT_URLS

    host = request.host_url
    # from meraki -request.arg
    BASE_GRANT_URL = request.args.get("base_grant_url")
    USER_CONTINUE_URL = request.args.get("user_continue_url")
    node_mac = request.args.get("node_mac")
    client_ip = request.args.get("client_ip")

    client_mac = request.args.get("client_mac")
    SUCCESS_URL = host + "success"

    return render_template(
        "connect.html",
        client_ip=client_ip,
        client_mac=client_mac,
        node_mac=node_mac,
        user_continue_url=USER_CONTINUE_URL,
        success_url=SUCCESS_URL
    )


@app.route("/login", methods=["POST"])
def get_login():
    password = request.form["password"]
    print(password)
    if is_password_in_db(password):
        remove_password_from_db(password)
        redirect_url = BASE_GRANT_URL + "?continue_url=" + SUCCESS_URL

        return redirect(redirect_url, code=302)
    else:
        return redirect("/failed", code=302)


@app.route("/success", methods=["GET"])
def get_success():
    return render_template(
        "success.html",
        user_continue_url=USER_CONTINUE_URL,
    )


@app.route("/failed", methods=["GET"])
def get_failed():
    return render_template("failed.html")


@app.errorhandler(404)
def page_not_found(error):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
   
