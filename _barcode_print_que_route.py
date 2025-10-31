from flask import render_template

@app.route('/barcode-print-que')
def barcode_print_que():
    return render_template('barcode_print_que.html')
