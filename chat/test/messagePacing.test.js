const test = require('node:test');
const assert = require('node:assert/strict');
const {createPacing} = require('../utils/messagePacing');
test('invalid settings retain finite bounded delays', () => {
 const p=createPacing({BOT_MIN_OUTBOUND_MS:'NaN', BOT_HUMANIZE_BASE_MS:'bad', BOT_HUMANIZE_MAX_MS:'Infinity', BOT_READING_PER_CHAR_MS:'-20'});
 assert.equal(p.outboundMin,850);
 for (const delay of [p.replyDelay('a'.repeat(10000)), p.readingDelay('hello'), p.receiptDelay(), p.firstDelay()]) assert.ok(Number.isFinite(delay) && delay>=0 && delay<=15000);
});
test('random delays respect configured bounds even when reversed', () => {
 const p=createPacing({BOT_READ_RECEIPT_MIN_MS:'1000',BOT_READ_RECEIPT_MAX_MS:'200',BOT_FIRST_TOUCH_MIN_MS:'400',BOT_FIRST_TOUCH_MAX_MS:'900'});
 assert.equal(p.receiptDelay(()=>0),1000);assert.equal(p.receiptDelay(()=>0.999),1000);
 assert.equal(p.firstDelay(()=>0),400);assert.equal(p.firstDelay(()=>0.998),899);
});
test('reply and reading maximums cap long text and zero disables waits', () => {
 const p=createPacing({BOT_HUMANIZE_MAX_MS:'2300',BOT_READING_MAX_MS:'1200'});
 assert.equal(p.replyDelay('a'.repeat(10000),()=>.99),2300);
 assert.equal(p.readingDelay('a'.repeat(10000),()=>.99),1200);
 assert.equal(createPacing({BOT_HUMANIZE_MAX_MS:'0'}).replyDelay('hello'),0);
});
