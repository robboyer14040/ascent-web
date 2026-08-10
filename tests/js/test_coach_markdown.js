'use strict';
/* coachMarkdown() — the renderer for AI Coach replies. The coach is prompted to
   answer in Markdown (week-by-week plans come back as tables), and the bubble
   previously showed escaped plain text, so `##` and `**` rendered literally.

   The security property that matters here: escHtml() runs on the raw text FIRST,
   so every transform below operates on already-escaped content and no model
   output can inject live markup. */

test('renders headings', function () {
  ok(coachMarkdown('## Week 1').indexOf('<h4 class="coach-h">Week 1</h4>') !== -1);
  ok(coachMarkdown('# Plan').indexOf('<h3 class="coach-h">Plan</h3>') !== -1);
});

test('renders bold, italic and inline code', function () {
  eq(coachMarkdown('**21 days**'), '<p><strong>21 days</strong></p>');
  eq(coachMarkdown('ride *easy* today'), '<p>ride <em>easy</em> today</p>');
  eq(coachMarkdown('use `Z2` pace'), '<p>use <code>Z2</code> pace</p>');
});

test('does not italicise mid-word asterisks', function () {
  // Bare ** inside a word must not become <em> and swallow the rest of the line.
  var out = coachMarkdown('4*8min efforts');
  ok(out.indexOf('<em>') === -1, 'unexpected <em> in: ' + out);
});

test('renders unordered and ordered lists', function () {
  eq(coachMarkdown('- ride\n- rest'), '<ul><li>ride</li><li>rest</li></ul>');
  eq(coachMarkdown('1. warm up\n2. go'), '<ol><li>warm up</li><li>go</li></ol>');
});

test('closes a list when prose resumes', function () {
  var out = coachMarkdown('- ride\n\nThen rest.');
  eq(out, '<ul><li>ride</li></ul><p>Then rest.</p>');
});

test('renders a training-plan table', function () {
  var md = '| Day | Session |\n|---|---|\n| Mon | Recovery |\n| Tue | OFF |';
  var out = coachMarkdown(md);
  ok(out.indexOf('<table class="coach-table">') === 0, 'no table: ' + out);
  eq(countOccurrences(out, '<th>'), 2);
  eq(countOccurrences(out, '<tr>'), 3);        // header + 2 body rows
  ok(out.indexOf('<td>Recovery</td>') !== -1);
  ok(out.indexOf('</tbody></table>') !== -1);
});

test('a pipe line without a separator row is not a table', function () {
  var out = coachMarkdown('costs | benefits');
  ok(out.indexOf('<table') === -1, 'unexpected table: ' + out);
});

test('renders fenced code blocks without marking up their contents', function () {
  var out = coachMarkdown('```\n**not bold**\n```');
  ok(out.indexOf('<pre class="coach-code">') !== -1, out);
  ok(out.indexOf('<strong>') === -1, 'code contents were marked up: ' + out);
});

test('escapes HTML before any markup is applied', function () {
  var out = coachMarkdown('<img src=x onerror=alert(1)>');
  ok(out.indexOf('<img') === -1, 'raw tag survived: ' + out);
  ok(out.indexOf('&lt;img') !== -1, out);
});

test('escapes HTML inside bold, tables and code', function () {
  ok(coachMarkdown('**<script>x</script>**').indexOf('<script') === -1);
  ok(coachMarkdown('| <b>a</b> |\n|---|\n| x |').indexOf('<b>') === -1);
  ok(coachMarkdown('`<i>hi</i>`').indexOf('<i>') === -1);
});

test('renders links but only http(s) ones', function () {
  ok(coachMarkdown('[docs](https://example.com)')
       .indexOf('<a href="https://example.com" target="_blank"') !== -1);
  // javascript: URLs must not become links.
  var out = coachMarkdown('[x](javascript:alert(1))');
  ok(out.indexOf('<a ') === -1, 'unexpected link: ' + out);
});

test('handles empty and plain input', function () {
  eq(coachMarkdown(''), '');
  eq(coachMarkdown(null), '');
  eq(coachMarkdown('Just a sentence.'), '<p>Just a sentence.</p>');
});

test('renders a horizontal rule', function () {
  eq(coachMarkdown('---'), '<hr>');
});

test('renders a realistic streamed reply end to end', function () {
  var md = [
    '# 21-Day Plan',
    '',
    '## Week 1',
    'You rode **54.3mi** yesterday.',
    '',
    '| Day | Session | HR |',
    '|---|---|---|',
    '| Mon | Recovery | < 105 bpm |',
    '',
    '- Keep Z2 easy',
    '- Rest Sunday',
  ].join('\n');
  var out = coachMarkdown(md);
  ok(out.indexOf('<h3 class="coach-h">21-Day Plan</h3>') !== -1);
  ok(out.indexOf('<h4 class="coach-h">Week 1</h4>') !== -1);
  ok(out.indexOf('<strong>54.3mi</strong>') !== -1);
  ok(out.indexOf('<table class="coach-table">') !== -1);
  ok(out.indexOf('<ul><li>Keep Z2 easy</li>') !== -1);
  ok(out.indexOf('&lt; 105 bpm') !== -1, 'the < in "< 105 bpm" must stay escaped');
});

test('partial markdown mid-stream does not throw', function () {
  // The bubble re-renders on every delta, so half-finished syntax is normal.
  var md = '# Plan\n\n| Day | Sess';
  for (var i = 1; i <= md.length; i++) coachMarkdown(md.slice(0, i));
  ok(true);
});
