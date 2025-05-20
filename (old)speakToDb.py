import sqlite3


def createRack():
    conn = sqlite3.connect('rack.db')
    cursor = conn.cursor()
    print("Rack starting creation")
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS INVENTORY (
                ID INTEGER PRIMARY KEY AUTOINCREMENT,
                ITEM_POSITION TEXT,
                BARCODE TEXT,
                IMAGES TEXT
            )
        ''')
        print("Rack created successfully")
        conn.commit()
        print("Rack committed")
        conn.close()
        print("Rack closed successfully")
    except sqlite3.Error as e:
        print("something went wrong with my Rack ",e)
        conn.close()


def createMyDataBase():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    print("Table starting creation")

# Create items table

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS INVENTORY (
                ID INTEGER PRIMARY KEY AUTOINCREMENT,
                Title TEXT,
                ItemID TEXT,
                SKU TEXT,
                Price TEXT,
                Quantity TEXT,
                Image TEXT,
                URL TEXT,
                List_State TEXT,
                Sold_Date TEXT,
                List_Date TEXT
            )
        ''')
        print("Table created successfully")
        conn.commit()
        print("table committed")
        conn.close()
        print("table closed successfully")
    except sqlite3.error as e:
        print("something went wrong with table ",e)
        conn.close()
createMyDataBase()
createRack()

def addToRack(ITEM_POSITION = None, BARCODE = None, IMAGES = None):
    conn = sqlite3.connect('rack.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO INVENTORY (ITEM_POSITION, BARCODE, IMAGES ) VALUES (?,?,?)", (ITEM_POSITION, BARCODE, IMAGES))
        conn.commit()
        print(f"added",{ITEM_POSITION},{BARCODE})
    except sqlite3.Error as e:
        print("something went wrong", e)
        try:
            conn.close()
            print("Closed successfully from myDataBase")
        except sqlite3.Error as e:
            print("Failed to close connection:", e)


def myDataBase(title, item_id, sku = None, price = None, quantity = None, image = None, List_State = None, Sold_Date = None, List_Date = None, URL = None):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()


    cursor.execute("SELECT 1 FROM INVENTORY WHERE ItemID = ?", (item_id,))
    item_exist = cursor.fetchone() is not None

    if item_exist:
        print("item already in inventory database, skipping")
        return

    try:
        cursor.execute("INSERT INTO INVENTORY (Title, ItemID, SKU, Price, Quantity, Image, List_State, Sold_Date, List_Date, URL ) VALUES (?,?,?,?,?,?,?,?,?,?)", (title, item_id, sku, price, quantity, image, List_State, Sold_Date, List_Date, URL))
        conn.commit()
        print(f"Added {title} successfully")
    except sqlite3.Error as e:
        print("something went wrong", e)
        try:
            conn.close()
            print("Closed successfully from myDataBase")
        except sqlite3.Error as e:
            print("Failed to close connection:", e)

    try:
        conn.close()
        print("Closed successfully from myDataBase")
    except sqlite3.Error as e:
        print("Failed to close connection:", e)















if __name__ == "__main__":
    print("hello world, I'm a database lol")









