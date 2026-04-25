import requests
from requests.auth import HTTPBasicAuth
import time
import getpass
import argparse
import itertools
# Import for Yaml config file support
import yaml
import sys
from pathlib import Path

# ---------------------------------------------------------
# Config to load variables from a YAML file and test the varibales
# ---------------------------------------------------------

import yaml
import sys
from pathlib import Path

def load_config(config_path: str) -> dict:
    """
    Load YAML configuration file and return as a dictionary.
    """
    try:
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"ERROR: Config file not found: {config_path}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"ERROR: Invalid YAML format: {e}")
        sys.exit(1)

# ---- EARLY CONFIG LOAD ----
CONFIG_FILE = Path("jqconfig.yaml")
config = load_config(CONFIG_FILE)

# Extract variables
EMAIL = config.get("email", "")
JIRA = config.get("jira", "")
USERNAMES = config.get("usernames", [])
DATA_CENTER = config.get("data_center", "")
LOC = config.get("loc", "")

# Optional: basic validation
if not isinstance(USERNAMES, list):
    raise ValueError("usernames must be a list")

if not DATA_CENTER or not LOC or not EMAIL:
    if not DATA_CENTER:
        raise ValueError("data_center must be defined in {CONFIG_FILE}")
    if not LOC:
        raise ValueError(f"loc must be defined in {CONFIG_FILE}")
    if not EMAIL:
        raise ValueError(f"EMAIL must be defined in {CONFIG_FILE}")    
    

# ---- REST OF YOUR SCRIPT ----
print("Usernames:", USERNAMES)
print("Data Center:", DATA_CENTER)
print("Location:", LOC)



# ---------------------------------------------------------
# Argument Parser
# ---------------------------------------------------------
parser = argparse.ArgumentParser(description="Jira Ticket Monitor with Slack + Auto Assignment")
parser.add_argument("-a", action="store_true", help="Enable auto round-robin assignment")
parser.add_argument("-s", action="store_true", help="Enable Slack notifications")
args = parser.parse_args()

AUTO_ASSIGN = args.a
SLACK_NOTIFY = args.s

# ---------------------------------------------------------
# Slack Webhook (ask ONLY if -s flag is used)
# ---------------------------------------------------------
SLACK_WEBHOOK = None
if SLACK_NOTIFY:
    SLACK_WEBHOOK = input("Enter your Slack webhook URL: ").strip()

# ---------------------------------------------------------
# Jira Auth
# ---------------------------------------------------------
JIRA_DOMAIN = "coreweave.atlassian.net"
API_TOKEN = JIRA
#EMAIL = input("Enter your Jira email: ").strip()
#API_TOKEN = getpass.getpass("Enter your Jira API token (input hidden): ").strip()


# ---------------------------------------------------------
# Round Robin Usernames (friendly names)
# ---------------------------------------------------------
ROUND_ROBIN_USERNAMES = USERNAMES
ROUND_ROBIN = itertools.cycle(ROUND_ROBIN_USERNAMES)

# ---------------------------------------------------------
# Resolve Jira accountIds dynamically
# ---------------------------------------------------------
def get_account_id(username):
    """
    Search Jira for the user's accountId based on their display name.
    Jira Cloud GDPR mode requires accountId for assignment.
    """
    search_url = f"https://{JIRA_DOMAIN}/rest/api/3/user/search"

    try:
        r = requests.get(
            search_url,
            auth=HTTPBasicAuth(EMAIL, API_TOKEN),
            params={"query": username}
        )
        users = r.json()

        if isinstance(users, list) and len(users) > 0:
            return users[0]["accountId"]  # Take the first match

        print(f"⚠ Could not find Jira account for '{username}'")
        return None

    except Exception as e:
        print(f"Error looking up accountId for {username}: {e}")
        return None

# ---------------------------------------------------------
# Slack Notification Function
# ---------------------------------------------------------
def send_slack_message(text):
    if not SLACK_NOTIFY:
        return
    try:
        payload = {"text": text}
        r = requests.post(SLACK_WEBHOOK, json=payload)
        if r.status_code != 200:
            print(f"Slack error: {r.status_code} {r.text}")
        else:
            print("🔔 Slack notification sent")
    except Exception as e:
        print(f"Slack request failed: {e}")

# ---------------------------------------------------------
# Assign Ticket
# ---------------------------------------------------------
def assign_ticket(issue_key):
    """Assign Jira ticket using dynamic accountId lookup."""
    username = next(ROUND_ROBIN)
    accountId = get_account_id(username)

    if not accountId:
        print(f"✖ Cannot assign {issue_key}: no accountId for {username}")
        return

    assign_payload = {"accountId": accountId}

    r = requests.put(
        f"https://{JIRA_DOMAIN}/rest/api/3/issue/{issue_key}/assignee",
        auth=HTTPBasicAuth(EMAIL, API_TOKEN),
        json=assign_payload
    )

    if r.status_code == 204:
        print(f"✔ Assigned {issue_key} to {username}")

        if SLACK_NOTIFY:
            send_slack_message(f"🚨 Ticket *{issue_key}* assigned to *{username}*")
    else:
        print(f"✖ Failed to assign {issue_key}: {r.text}")

# ---------------------------------------------------------
# JQL Query (only DO- tickets)
# ---------------------------------------------------------
JQL = (
    'issuekey ~ "DO-*" AND assignee = EMPTY AND type = "Service Request" '
    'AND statusCategory != "Done" AND ('
    f'"Asset LOC" = "{DATA_CENTER}" OR '
    f'"Asset Data Center" ~ "{LOC}"'
    ')'
)

SEARCH_URL = f"https://{JIRA_DOMAIN}/rest/api/3/search/jql"

# ---------------------------------------------------------
# Loop Settings interval is in seconds and duration is in hours
# ---------------------------------------------------------

interval_seconds = config.get("interval_seconds", "")
duration_hours = config.get("duration_hours", "")
#interval_seconds = 30
#duration_hours = 8
end_time = time.time() + duration_hours * 3600

print("\nStarting Jira monitor...\n")
if AUTO_ASSIGN:
    print("Auto-assignment ENABLED (-a)")
if SLACK_NOTIFY:
    print("Slack notifications ENABLED (-s)")
print("")

# ---------------------------------------------------------
# Main Loop
# ---------------------------------------------------------
while time.time() < end_time:
    try:
        response = requests.get(
            SEARCH_URL,
            auth=HTTPBasicAuth(EMAIL, API_TOKEN),
            params={"jql": JQL, "fields": ["key", "summary", "assignee", "status"]}
        )

        if response.status_code == 200:
            data = response.json()
            issues = data.get("issues", [])

            if issues:
                for issue in issues:
                    key = issue["key"]
                    summary = issue["fields"]["summary"]
                    assignee = issue["fields"]["assignee"]
                    assignee_name = assignee["displayName"] if assignee else "Unassigned"
                    status_name = issue["fields"]["status"]["name"]

                    print(f"{key} | {summary} | {assignee_name} | {status_name}")

                    # -------------------------------------------
                    # Behavior Matrix:
                    #   -s only   → Slack notify only
                    #   -a only   → Assign only
                    #   -a -s     → Assign + Slack
                    #   neither   → Print only
                    # -------------------------------------------
                    if SLACK_NOTIFY and not AUTO_ASSIGN:
                        send_slack_message(f"🔎 New unassigned ticket: *{key}* — {summary}")

                    elif AUTO_ASSIGN:
                        assign_ticket(key)

            else:
                print("No matching issues found at this time.")

        else:
            print(f"Error {response.status_code}: {response.text}")

    except Exception as e:
        print(f"Request failed: {e}")

    time.sleep(interval_seconds)

print(f"\nFinished {duration_hours}-hour loop.")
