'use strict';
/**
 * Tests de límites operativos configurables del bot admin.
 *
 * Verifican que `botMaxPrice` y `botMaxPointsAdjust`:
 *   - Leen del cfg local (sincronizado desde `/branding`).
 *   - Aplican cap defensivo interno cuando la config es absurda.
 *   - Caen a un default sensato ante inputs no numéricos.
 *
 * Antes vivían como hardcodes 1000/9999/10000 dispersos por bot.js.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const dbDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oxidian-bot-limits-'));
process.env.BOT_TEST_MODE = '1';
process.env.DB_DIR = dbDir;
process.env.NODE_ENV = 'test';
process.env.SIMULATE_EVO_SEND = 'true';
process.env.OXIDIAN_KEY = 'k';
process.env.BOT_PANEL_KEY = 'k';
process.env.WEBHOOK_SECRET = 'w';
process.env.OWNER_NUMBER = '34600000001';
process.env.SUPERADMINS = '34600000002';

const { _test } = require('../bot');
const { botMaxPrice, botMaxPointsAdjust, setCfg } = _test;

test('botMaxPrice devuelve el valor sincronizado desde branding', () => {
  setCfg('bot_max_price_eur', '1500');
  assert.equal(botMaxPrice(), 1500);
});

test('botMaxPrice aplica cap defensivo superior (100000)', () => {
  setCfg('bot_max_price_eur', '999999');
  assert.equal(botMaxPrice(), 100000);
});

test('botMaxPrice aplica cap defensivo inferior (1)', () => {
  setCfg('bot_max_price_eur', '0');
  assert.equal(botMaxPrice(), 1);
});

test('botMaxPrice cae a 9999 default ante valor no numérico', () => {
  setCfg('bot_max_price_eur', 'no-es-numero');
  assert.equal(botMaxPrice(), 9999);
});

test('botMaxPointsAdjust devuelve valor sincronizado', () => {
  setCfg('bot_max_points_adjust', '5000');
  assert.equal(botMaxPointsAdjust(), 5000);
});

test('botMaxPointsAdjust cap defensivo superior (1000000)', () => {
  setCfg('bot_max_points_adjust', '99999999');
  assert.equal(botMaxPointsAdjust(), 1000000);
});

test('botMaxPointsAdjust cap defensivo inferior (1)', () => {
  setCfg('bot_max_points_adjust', '0');
  assert.equal(botMaxPointsAdjust(), 1);
});

test('botMaxPointsAdjust cae a 10000 default ante valor inválido', () => {
  setCfg('bot_max_points_adjust', 'xyz');
  assert.equal(botMaxPointsAdjust(), 10000);
});
