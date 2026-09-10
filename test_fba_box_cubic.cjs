const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('templates/fba_prep.html','utf8');
const state={amazon:{operation_warnings:{box_minimums:{'BOX-11':{weight_lb:26.81,volume_in3:5633.25},'BOX-10':{weight_lb:17.37}}}}};
const context=vm.createContext({state,esc:v=>String(v)});
function block(a,b){return html.slice(html.indexOf(a),html.indexOf(b,html.indexOf(a)))}
vm.runInContext(block('    const boxCubicInches=','    function boxContentsHtml('),context);

// A carton with no measurements yet says so instead of showing a bogus zero.
assert.match(context.boxCubicText({local_id:'BOX-01'}).text,/shows once length/);

// Amazon quoted no minimum for this carton: cubic only, no pass/fail claim.
const plain=context.boxCubicText({local_id:'BOX-01',length_in:19,width_in:16,height_in:15,weight_lb:31.5});
assert.equal(plain.text,'Cubic 4,560 in³');
assert.equal(plain.kind,'ok');

// Short on both weight and volume: both shortfalls are quantified.
const short=context.boxCubicText({local_id:'BOX-11',length_in:19,width_in:15,height_in:15,weight_lb:18});
assert.equal(short.kind,'warn');
assert.match(short.text,/Cubic 4,275 in³/);
assert.match(short.text,/at least 5,633.25 in³ — short by 1,358.25 in³/);
assert.match(short.text,/at least 26.81 lb — short by 8.81 lb/);

// Weight-only minimum, now met after a reweigh.
const fixed=context.boxCubicText({local_id:'BOX-10',length_in:15,width_in:13,height_in:13,weight_lb:18});
assert.equal(fixed.kind,'ok');
assert.match(fixed.text,/at least 17.37 lb — met/);
assert.doesNotMatch(fixed.text,/in³ — /);

// A bigger carton clears the volume shortfall.
const bigger=context.boxCubicText({local_id:'BOX-11',length_in:20,width_in:16,height_in:18,weight_lb:27});
assert.equal(bigger.kind,'ok');
assert.match(bigger.text,/Cubic 5,760 in³/);
console.log('Carton cubic readout, Amazon minimums, and shortfall maths passed.');
