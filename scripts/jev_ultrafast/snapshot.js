(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  // Last DOM mutation time. Post-input waits read it to let transitions finish before the next decision.
  if (!cache.observer) {
    cache.changed=performance.now();
    cache.observer=new MutationObserver(()=>{cache.changed=performance.now()});
    cache.observer.observe(document,{subtree:true,childList:true,attributes:true,characterData:true});
  }
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  // A checkbox or radio styled away (opacity 0, 1 px, off-screen, under its label) is operated through its
  // visible label; clicking a label activates its control.
  cache.proxy = e => ['checkbox','radio'].includes(e.type) ? [...(e.labels||[])].find(visible) || null : null;
  // A viewport point where a click lands on e itself: its centre, the centres of its fragments (a link
  // wrapped over two lines has its box centre between them), then a small grid, plus `random` points.
  // The snapshot offers only controls with such a point and the executor clicks one, so a control clipped
  // by a scroll container or under a banner is not offered only to be refused at input time.
  cache.hit = (e,random=0) => {
    const r=e.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    const pts=[[r.x+r.width/2,r.y+r.height/2]];
    for (const q of e.getClientRects()) pts.push([q.x+q.width/2,q.y+q.height/2]);
    for (const fx of [0.2,0.5,0.8]) for (const fy of [0.25,0.75]) pts.push([r.x+r.width*fx,r.y+r.height*fy]);
    for (let i=0;i<random;i++) pts.push([r.x+Math.random()*r.width,r.y+Math.random()*r.height]);
    for (const [x,y] of pts)
      if (x>=0 && y>=0 && x<innerWidth && y<innerHeight && lands(e,document.elementFromPoint(x,y))) return {x,y};
    return null;
  };
  // A click on h reaches e's widget: h is e or inside it, or h is e's own visual layer. Result rows often put
  // their visible content in a sibling above an accessible link and handle clicks on a shared container (Google
  // Flights). A layer counts only if it lies within e's box, shares an ancestor with e below <body>, and is not
  // part of another control; a banner, consent frame, or page column covering e fails at least one of these.
  const lands = (e,h) => {
    if (!h || e.contains(h)) return !!h;
    if (h===document.body || h===document.documentElement) return false;
    const r=e.getBoundingClientRect(), q=h.getBoundingClientRect();
    if (q.left<r.left-2 || q.top<r.top-2 || q.right>r.right+2 || q.bottom>r.bottom+2) return false;
    let common=h.parentElement;
    while (common && !common.contains(e)) common=common.parentElement;
    if (!common || common===document.body || common===document.documentElement) return false;
    const other=h.closest(selector);
    return !other || other.contains(e);
  };
  cache.point = (e,random=0) => (visible(e) && cache.hit(e,random)) ||
    (cache.proxy(e) && cache.hit(cache.proxy(e),random)) || null;
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(document.getElementById(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  // Chrome renders these as segmented spinners that ignore typed text; they take an ISO value (upstream #116).
  const formats={date:'YYYY-MM-DD',time:'HH:MM','datetime-local':'YYYY-MM-DDTHH:MM',month:'YYYY-MM',week:'YYYY-Www'};
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type) || e.type in formats) return 'textbox';
    }
    return null;
  };
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    [...document.querySelectorAll('input,textarea,select')].filter(safe)
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly])];
  cache.guard=e=>{
    if (!e?.isConnected || !(visible(e) || cache.proxy(e))) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('aria-pressed'),e.getAttribute('href'),scope?.innerText?.slice(0,6000)||'',
      // Option meaning must not depend on the truncated scope text above.
      e.tagName==='SELECT' ? [...e.options].map(o=>[o.value,o.label,o.disabled||!!o.closest('optgroup[disabled]')]) : null];
  };
  const actions=[];
  for (const e of document.querySelectorAll(selector)) {
    if (!safe(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const inView=q=>{const r=q.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
      return r.width>0 && r.height>0 && x>=0 && y>=0 && x<innerWidth && y<innerHeight};
    const box=visible(e) && inView(e) ? e : cache.proxy(e) && inView(cache.proxy(e)) ? cache.proxy(e) : null;
    const rname=role(e);
    if (!box || !rname) continue;
    const r=box.getBoundingClientRect();
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    const base={node:identity(e),role:rname,label:name(e)||rname,
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    // Occlusion is geometry: dropped from the offer after the marker, which compares meaning only.
    if (!cache.point(e)) base.covered=true;
    for (const key of ['checked','selected','expanded','pressed']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        actions.push({...base,kind:'select',value:o.value,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
    } else {
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (['textbox','searchbox','spinbutton'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      const format=e.tagName==='INPUT' && formats[e.type];
      actions.push({...base,kind:editable?'fill':'click',value,...(format ? {format} : {})});
      // A segmented control's calendar is browser UI, not DOM, so opening it would be a dead end.
      if (editable && !format) actions.push({...base,kind:'click',value,label:'Open '+base.label});
    }
  }
  const words=[], walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  const range=document.createRange(); let node,length=0;
  while ((node=walker.nextNode()) && length<6000) {
    const value=node.textContent.trim(), parent=node.parentElement;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node); const r=range.getBoundingClientRect();
    if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
      words.push(value); length+=value.length;
    }
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,covered,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6]];
  actions.splice(0,actions.length,...actions.filter(a=>!a.covered));
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  // Fields the model could choose. A fill may follow focus only into a field outside this set (browser.py).
  cache.offered=new Set(actions.map(a=>a.node));
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  // Document age lets the executor wait out post-load script churn only where it happens.
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,age:Math.round(performance.now()),
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions};
})()
