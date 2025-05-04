from flask import Flask, render_template, request
from inventory import find_item  # adjust this to match your actual import

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/search", methods=["POST"])
def search():
    query = request.form["query"]
    result = find_item(query)
    return render_template("index.html", result=result)

if __name__ == "__main__":
    app.run()
