import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Define scope and credentials
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
#declare sheets
spreadsheet = client.open_by_key('1fADOyZRI16dJEGJnOZS7NnI5glKHStKfhAYTFImAUp8')
worksheet = spreadsheet.worksheet('Sheet1')
inventory = worksheet.col_values(1)
#variables
shelfCodes = ('a','b','c','d')
myDict = {'a': [] ,'b' : [],'c' : [],'d' : [], }
currentShelf = None

def update_inventory():

    for item in inventory:
        if item in shelfCodes:
            currentShelf = item
        elif currentShelf:
                myDict[currentShelf].append(item)

    return myDict

def find_item(barcode):

    currentInventory = update_inventory()

    for item in currentInventory:
        position = item
        for item in currentInventory[item]:
            if item == barcode:
                print(position, 'hello')
                return position





