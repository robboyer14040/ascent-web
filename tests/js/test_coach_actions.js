'use strict';
/* coachAppendMessage() — every coach reply must carry Copy and PDF actions for
   its exchange, and must record the message id those actions need. The athlete
   should be able to extract a single exchange or the whole conversation, so a
   reply rendered without these buttons is a broken feature, not a cosmetic one. */

function _messagesContainer() {
  var c = document.createElement('div');
  document._byId['coach-messages'] = c;
  return c;
}

test('a coach reply renders both exchange actions', function () {
  _messagesContainer();
  var wrap = coachAppendMessage('assistant', 'Ride Z2 tomorrow.', 1700000000, 42);
  ok(wrap.innerHTML.indexOf('coachCopyExchange(this)') !== -1, 'no Copy button');
  ok(wrap.innerHTML.indexOf('coachExchangePdf(this)') !== -1, 'no PDF button');
  ok(wrap.innerHTML.indexOf('coach-actions') !== -1);
});

test('the reply carries its message id for the PDF endpoint', function () {
  _messagesContainer();
  var wrap = coachAppendMessage('assistant', 'Answer', 1700000000, 42);
  eq(wrap.dataset.msgId, 42);
});

test('every coach reply gets actions, not just the newest', function () {
  var c = _messagesContainer();
  coachAppendMessage('user', 'q1', 1700000000, 1);
  coachAppendMessage('assistant', 'a1', 1700000001, 2);
  coachAppendMessage('user', 'q2', 1700000002, 3);
  coachAppendMessage('assistant', 'a2', 1700000003, 4);

  var replies = c.children.filter(function (el) {
    return el.className.indexOf('assistant') !== -1;
  });
  eq(replies.length, 2);
  replies.forEach(function (el, i) {
    ok(el.innerHTML.indexOf('coachCopyExchange') !== -1, 'reply ' + i + ' has no Copy');
    ok(el.innerHTML.indexOf('coachExchangePdf') !== -1, 'reply ' + i + ' has no PDF');
  });
});

test('the athlete\'s own messages get no action buttons', function () {
  _messagesContainer();
  var wrap = coachAppendMessage('user', 'How should I train?', 1700000000, 7);
  ok(wrap.innerHTML.indexOf('coach-actions') === -1,
     'actions belong to the reply, which covers the whole exchange');
});

test('system messages are not rendered at all', function () {
  _messagesContainer();
  eq(coachAppendMessage('system', 'internal', 1700000000, 9), undefined);
});

test('a reply with no id still renders (PDF reports it is unsaved)', function () {
  _messagesContainer();
  var wrap = coachAppendMessage('assistant', 'Streaming…', 1700000000, null);
  ok(wrap.innerHTML.indexOf('coachExchangePdf') !== -1);
  ok(!wrap.dataset.msgId);
});

test('reply body is rendered as Markdown, the question as plain text', function () {
  _messagesContainer();
  var reply = coachAppendMessage('assistant', '## Week 1', 1700000000, 1);
  ok(reply.innerHTML.indexOf('<h4 class="coach-h">Week 1</h4>') !== -1);

  var asked = coachAppendMessage('user', '## not a heading', 1700000000, 2);
  ok(asked.innerHTML.indexOf('<h4') === -1, 'user text must not be parsed as Markdown');
});

test('model output cannot inject markup into the bubble', function () {
  _messagesContainer();
  var wrap = coachAppendMessage('assistant', '<img src=x onerror=alert(1)>', 1700000000, 1);
  ok(wrap.innerHTML.indexOf('<img') === -1, 'raw tag survived: ' + wrap.innerHTML);
});
