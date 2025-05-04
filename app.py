from flask import Flask, render_template, request
from inventory import find_item  # adjust this to match your actual import
from shelfHighlighter import highLight
app = Flask(__name__)

@app.route("/")
def home():
    shelf = request.args.get("shelf")
    return render_template("index.html", shelf=shelf)

@app.route("/highLight")
def highLight():
    shelf = request.args.get("shelf")




@app.route("/search", methods=["POST"])
def search():
    query = request.form["query"]
    result = find_item(query)
    return render_template("index.html", search_result=result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
