'use strict';
const fs = require('fs');
const assert = require('assert');
const vm = require('vm');
const html = fs.readFileSync('articles.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/)[1];
const source = JSON.parse(fs.readFileSync('json/articles.json', 'utf8'));
const published = source.filter(a => a.status === 'published' && ['beauty','dailygoods','gadget'].includes(a.category));
const list = {items: [], replaceChildren() {this.items=[];}, appendChild(el) {this.items.push(el);}};
const buttons = Object.fromEntries(['all','beauty','dailygoods','gadget'].map(id=>['btn-'+id, {classList:{active:true, add(k){this[k]=true;}, remove(k){this[k]=false;}}}]));
function element(tag) {
 return {tag,children:[],append(...xs){this.children.push(...xs);},appendChild(child){this.children.push(child);}};
}
const document = {
 getElementById(id){return id==='articleList'?list:buttons[id];},
 querySelectorAll(selector){assert.equal(selector,'.filter-controls button');return Object.values(buttons);},
 createElement:element
};
const context = {document,console,Math, fetch: async url => {assert.equal(url,'/json/articles.json');return {ok:true,json:async()=>source};}};
vm.createContext(context);
vm.runInContext(script, context);
const flush = () => new Promise(resolve=>setTimeout(resolve,5));
(async()=>{
 await flush();
 assert.equal(list.items.length,published.length);
 const getCategory = card => card.href.split('/')[2];
 for(const [category,expected] of [['beauty','美容'],['dailygoods','日用品'],['gadget','家電']]){
   vm.runInContext(`filterCategory('${category}')`,context);
   assert.equal(list.items.length,published.filter(a=>a.category===category).length);
   assert(list.items.every(card=>getCategory(card)===category));
   assert(list.items[0].children[1].children[0].textContent===expected);
   vm.runInContext('shuffleCurrentView()',context);
   assert(list.items.every(card=>getCategory(card)===category));
 }
 vm.runInContext("filterCategory('all')",context);
 assert.equal(list.items.length,published.length);
 vm.runInContext('shuffleCurrentView()',context);
 assert.equal(list.items.length,published.length);
 console.log('ARTICLE_CATALOG_RUNTIME_PASS',list.items.length,'articles');
})().catch(error=>{console.error(error);process.exitCode=1;});
