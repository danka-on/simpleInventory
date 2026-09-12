const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const spoken = [];
const status = { textContent: '' };
const window = {
    SpeechSynthesisUtterance: function (text) { this.text = text; },
    speechSynthesis: {
        resume() {}, cancel() {},
        speak(utterance) { spoken.push(utterance.text); utterance.onend(); }
    }
};
vm.runInNewContext(fs.readFileSync('static/scan-feedback.js', 'utf8'), {
    window,
    document: { getElementById: () => status, documentElement: { lang: 'en' }, addEventListener() {} },
    navigator: {}, setTimeout, clearTimeout
});
const feedback = window.ScanFeedback;
assert.equal(feedback.inspect('123456789012').length, 0);
assert.equal(feedback.inspect('123456789012-3').length, 0);
assert.equal(feedback.inspect('12345678').length, 0);
assert.equal(feedback.inspect('1234567890123').length, 0);
// One leading zero is normal on a UPC-A label, so it must not interrupt a scan.
assert.equal(feedback.inspect('012345678901').length, 0);
assert.equal(feedback.inspect('01234567').length, 0);
assert.match(feedback.inspect('01234')[0], /Check the length/);
assert.match(feedback.inspect('000123456789')[0], /3 zeroes/);
assert.equal(feedback.inspect('000000123456789012').length, 2);
for (const length of [7, 9, 10, 11, 14, 16]) {
    assert.match(feedback.inspect('1'.repeat(length))[0], /Check the length/);
}
assert.match(feedback.inspect('123456789012-0')[0], /invalid suffix/);
assert.match(feedback.inspect('abc')[0], /unexpected characters/);
(async () => {
    await feedback.check('000123456789');
    assert.match(status.textContent, /3 zeroes/);
    assert.match(spoken.at(-1), /3 zeroes/);
    await feedback.needsInfo(true);
    assert.match(spoken.at(-1), /needs a name/);
    await feedback.needsInfo(false, '123');
    assert.match(spoken.at(-1), /thumbnail/);
    const count = spoken.length;
    await feedback.needsInfo(false, '123');
    assert.equal(spoken.length, count, 'Repeated stock scan should not repeat thumbnail speech');
    await feedback.check('123456789012');
    assert.equal(status.textContent, '');
    delete window.speechSynthesis;
    await feedback.check('123');
    assert.match(status.textContent, /3 digits/, 'Warnings survive missing browser speech support');
    console.log('Scan feedback checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
