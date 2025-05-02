import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Define scope and credentials
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)

shelfCodes = ['a','b','c','d']
myDict = {'a': [] ,'b' : [],'c' : [],'d' : [], }


#hmm

spreadsheet = client.open_by_key('1fADOyZRI16dJEGJnOZS7NnI5glKHStKfhAYTFImAUp8')
worksheet = spreadsheet.worksheet('Sheet1')

#print(myDict['a'])
#print(worksheet.get_all_records())

inventory = worksheet.col_values(1) 
currentShelf = None



#def update_inventory:
    #goes through the first google sheets column. 
    #looks for a designated shelf barcode defined in shelfCodes[].
    #shelfCodes[] match the keys to my dictionary myDict , each corresponding key gets updated with a value other than a shelfCode[], 
    #which should be an item barcode.

for item in inventory:
        

    if item in shelfCodes:
        currentShelf = item

    elif currentShelf:
            myDict[currentShelf].append(item)

        #if currentShelf in shelfCodes and item not in shelfCodes:
         #   myDict[currentShelf].append(item)
        

print(myDict)




    






'''
# Open a spreadsheet by name
def open_spreadsheet(spreadsheet_name):
    return client.open(spreadsheet_name)

# Read all data from a worksheet
def read_worksheet(spreadsheet, worksheet_name):
    worksheet = spreadsheet.worksheet(worksheet_name)
    return worksheet.get_all_records()

# Write data to a specific cell
def write_to_cell(spreadsheet, worksheet_name, cell, value):
    worksheet = spreadsheet.worksheet(worksheet_name)
    worksheet.update(cell, value)

# Append a row to a worksheet
def append_row(spreadsheet, worksheet_name, row_data):
    worksheet = spreadsheet.worksheet(worksheet_name)
    worksheet.append_row(row_data)

# Example usage
if __name__ == "__main__":
    # Replace 'Your Spreadsheet Name' with your Google Sheet name
    spreadsheet = open_spreadsheet("Your Spreadsheet Name")
    
    # Read data from a worksheet named 'Sheet1'
    data = read_worksheet(spreadsheet, "Sheet1")
    print("Data from Sheet1:", data)
    
    # Write to a specific cell (e.g., A1)
    write_to_cell(spreadsheet, "Sheet1", "A1", "Updated Value")
    
    # Append a new row
    new_row = ["Column1 Value", "Column2 Value", "Column3 Value"]
    append_row(spreadsheet, "Sheet1", new_row)

    '''