/* Carte du savoir — RAG-Bachelor
 * Aucune dépendance. Canvas 2D + simulation de forces maison.
 *
 * Montage : tout élément [data-carte-savoir] contenant le balisage du template.
 *   data-source="/api/carte-savoir"     URL JSON (même origine, cookies de session envoyés)
 *   data-source="#cs-donnees"           ou id d'un <script type="application/json"> rendu côté serveur
 *   data-revision-url="/reviser?concept={id}"   (optionnel) page de révision d'un concept
 * Sans data-revision-url, « Réviser ce concept » simule une révision locale (mode démo).
 *
 * Sécurité : aucun innerHTML ; tous les textes passent par textContent ou fillText.
 */
(function(){
'use strict';

var PALETTE = ['#2F8FD8','#0F9B73','#D98B00','#B9579A','#D2551A','#8A9A00','#5B6CC9','#2A9DA5'];
var TAU = Math.PI*2;
var reduit = window.matchMedia('(prefers-reduced-motion: reduce)');
var sombreMq = window.matchMedia('(prefers-color-scheme: dark)');

function pct(x){ return Math.round(x*100)+'\u202F%'; }
function rgba(hex,a){ var v=parseInt(hex.slice(1),16); return 'rgba('+(v>>16&255)+','+(v>>8&255)+','+(v&255)+','+a+')'; }
function norm(s){ return String(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase(); }
function hexValide(c){ return typeof c==='string' && /^#[0-9a-fA-F]{6}$/.test(c); }
function clamp(x,a,b){ return Math.min(b,Math.max(a,x)); }
function hash(str){ var h=2166136261; str=String(str); for(var i=0;i<str.length;i++){ h^=str.charCodeAt(i); h=Math.imul(h,16777619); } return h>>>0; }
function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;var t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}

function CarteSavoir(racine){
  var q = function(nom){ return racine.querySelector('[data-cs="'+nom+'"]'); };
  var canvas = q('canvas'), ctx = canvas.getContext('2d');
  var source = racine.getAttribute('data-source');
  var urlRevision = racine.getAttribute('data-revision-url');
  var titre = '';
  var MODULES = [], noeuds = [], liens = [];
  var T = {}, W = 1, H = 1, DPR = 1;
  var cam = {s:1,tx:0,ty:0}, camCible = {s:1,tx:0,ty:0};
  var alpha = 1, debut = null, recadre = false, enMarche = false;
  var etat = {survol:null, selection:null, module:null, recherche:[]};
  var ondes = [], particules = [], dernierFlux = 0, toastT = null;

  /* ---------- Thème ---------- */
  function estSombre(){ var t=document.documentElement.getAttribute('data-theme'); if(t==='dark') return true; if(t==='light') return false; return sombreMq.matches; }
  function lireTheme(){
    var cs = getComputedStyle(racine);
    ['fond','panneau','trait','texte','texte-doux','lien','eteint'].forEach(function(k){ T[k]=cs.getPropertyValue('--cs-'+k).trim(); });
    T.sombre = estSombre();
    T.police = cs.fontFamily;
  }
  lireTheme();
  sombreMq.addEventListener('change',lireTheme);
  new MutationObserver(lireTheme).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','class']});
  if(document.fonts && document.fonts.ready) document.fonts.ready.then(lireTheme);

  /* ---------- Taille ---------- */
  function redim(){
    var r = canvas.getBoundingClientRect();
    W = Math.max(1,r.width); H = Math.max(1,r.height); DPR = Math.min(window.devicePixelRatio||1,2);
    canvas.width = Math.round(W*DPR); canvas.height = Math.round(H*DPR);
  }
  redim();
  window.addEventListener('resize',redim);
  if('ResizeObserver' in window) new ResizeObserver(redim).observe(racine);

  /* ---------- Messages ---------- */
  function toast(msg){ var t=q('toast'); t.textContent=msg; t.classList.add('cs-on'); clearTimeout(toastT); toastT=setTimeout(function(){t.classList.remove('cs-on');},2600); }
  function afficherEtat(message, reessayer){
    var e = q('etat'); e.textContent='';
    var p = document.createElement('p'); p.textContent = message; e.appendChild(p);
    if(reessayer){ var b=document.createElement('button'); b.type='button'; b.className='cs-btn'; b.textContent='Réessayer'; b.addEventListener('click',function(){ e.hidden=true; charger(); }); e.appendChild(b); }
    e.hidden = false;
  }

  /* ---------- Données ---------- */
  function charger(){
    if(!source){ afficherEtat('Source de données manquante (attribut data-source).',false); return; }
    var promesse;
    if(source.charAt(0)==='#'){
      var el = document.getElementById(source.slice(1));
      promesse = el ? Promise.resolve().then(function(){ return JSON.parse(el.textContent); }) : Promise.reject(new Error('bloc JSON introuvable'));
    }else{
      promesse = fetch(source,{credentials:'same-origin',headers:{'Accept':'application/json'}}).then(function(r){
        if(!r.ok) throw new Error('HTTP '+r.status); return r.json();
      });
    }
    promesse.then(construire).catch(function(err){
      console.error('[carte-savoir]',err);
      afficherEtat('Impossible de charger la carte du savoir.',true);
    });
  }

  function construire(d){
    if(!d || !Array.isArray(d.modules) || !Array.isArray(d.concepts) || !Array.isArray(d.liens)) throw new Error('format de données invalide');
    titre = typeof d.titre==='string' ? d.titre : '';
    var parId = {}, modParId = {};
    MODULES = d.modules.map(function(m,i){
      var mod = {id:String(m.id), nom:String(m.nom||m.id), couleur: hexValide(m.couleur)?m.couleur:PALETTE[i%PALETTE.length], noeuds:[]};
      modParId[mod.id] = mod; return mod;
    });
    var aujourdHui = new Date(); aujourdHui.setHours(0,0,0,0);
    noeuds = [];
    d.concepts.forEach(function(c){
      var m = modParId[String(c.module)];
      if(!m){ m = {id:String(c.module), nom:String(c.module), couleur:PALETTE[MODULES.length%PALETTE.length], noeuds:[]}; modParId[m.id]=m; MODULES.push(m); }
      var vu = typeof c.maitrise==='number' && isFinite(c.maitrise);
      var jours = null;
      if(c.prochaine_revision){ var t = Date.parse(c.prochaine_revision); if(!isNaN(t)) jours = Math.round((t-aujourdHui.getTime())/864e5); }
      var aRevoir = !!c.a_revoir || (vu && jours!==null && jours<=0);
      var r = mulberry32(hash(c.id));
      var n = {id:String(c.id), nom:String(c.nom||c.id), module:m, maitrise: vu?clamp(c.maitrise,0,1):0, mAff:0, vu:vu,
        aRevoir:aRevoir, jours:jours, voisins:new Set(), x:0, y:0, vx:0, vy:0, fx:null, fy:null,
        ph1:r()*6.28, ph2:r()*6.28, j1:r()-.5, j2:r()-.5, vis:0, zoom:1};
      parId[n.id] = n; noeuds.push(n); m.noeuds.push(n);
    });
    liens = [];
    d.liens.forEach(function(l){
      var a = parId[String(l.source)], b = parId[String(l.target)];
      if(!a||!b||a===b||a.voisins.has(b)) return;
      a.voisins.add(b); b.voisins.add(a);
      liens.push({a:a,b:b,croise:a.module!==b.module,sens:(liens.length%2?1:-1)});
    });
    MODULES = MODULES.filter(function(m){ return m.noeuds.length>0; });
    if(!noeuds.length){ afficherEtat('Aucun concept pour l’instant. Importez un cours pour construire votre carte.',false); return; }

    // Ancres des modules sur une ellipse (verticale en portrait)
    var portrait = H > W*1.1, k = MODULES.length;
    var rx = (portrait?175:300)*Math.max(1,Math.sqrt(k/6)), ry = (portrait?290:200)*Math.max(1,Math.sqrt(k/6));
    MODULES.forEach(function(m,i){
      var ang = k===1 ? 0 : i/k*TAU - Math.PI/2;
      m.ax = k===1 ? 0 : Math.cos(ang)*rx; m.ay = k===1 ? 0 : Math.sin(ang)*ry;
    });
    noeuds.forEach(function(n){ n.x = n.module.ax+n.j1*30; n.y = n.module.ay+n.j2*30; n.r = 8+Math.sqrt(n.voisins.size)*2.6; });
    recalculer(); renderModules();
    cadrer(MODULES.map(function(m){ return {x:m.ax,y:m.ay,r:90}; }),true);
    if(reduit.matches){ for(var i=0;i<420;i++) tick(0); }
    if(!enMarche){ enMarche = true; requestAnimationFrame(boucle); }
  }

  function recalculer(){
    MODULES.forEach(function(m){
      m.maitrise = m.noeuds.reduce(function(s,n){return s+n.maitrise;},0)/m.noeuds.length;
      m.nbRevoir = m.noeuds.filter(function(n){return n.aRevoir;}).length;
    });
  }
  function texteRevision(n){
    if(!n.vu) return 'pas encore révisé';
    if(n.aRevoir || (n.jours!==null && n.jours<=0)) return 'aujourd’hui';
    if(n.jours===null) return 'non planifiée';
    return n.jours===1 ? 'demain' : 'dans '+n.jours+' jours';
  }

  /* ---------- Caméra ---------- */
  function zone(){
    var bureau = W>900, z = {l:16,r:W-16,t:130,b:H-120};
    if(bureau){ z.l = 24+290+24; z.t = 90; z.b = H-24; if(!q('detail').hidden) z.r = W-24-330-24; }
    else if(!q('detail').hidden) z.b = H*0.45;
    return z;
  }
  function cadrer(liste, instant){
    if(!liste.length) return;
    var x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
    liste.forEach(function(n){ x0=Math.min(x0,n.x-n.r-70); x1=Math.max(x1,n.x+n.r+110); y0=Math.min(y0,n.y-n.r-50); y1=Math.max(y1,n.y+n.r+40); });
    var z = zone(), s = clamp(Math.min((z.r-z.l)/(x1-x0),(z.b-z.t)/(y1-y0),1.8),.25,3);
    camCible.s = s; camCible.tx = (z.l+z.r)/2-(x0+x1)/2*s; camCible.ty = (z.t+z.b)/2-(y0+y1)/2*s;
    if(instant || reduit.matches){ cam.s=camCible.s; cam.tx=camCible.tx; cam.ty=camCible.ty; }
  }
  function centrerSur(n){
    var z = zone(), s = Math.max(camCible.s,1.25);
    camCible.s = s; camCible.tx = (z.l+z.r)/2 - n.x*s; camCible.ty = (z.t+z.b)/2 - n.y*s;
  }
  function zoomer(f,px,py){
    var z = zone(); px = px==null?(z.l+z.r)/2:px; py = py==null?(z.t+z.b)/2:py;
    var s = clamp(camCible.s*f,.25,3), k = s/camCible.s;
    camCible.tx = px-(px-camCible.tx)*k; camCible.ty = py-(py-camCible.ty)*k; camCible.s = s;
    if(reduit.matches){ cam.s=camCible.s; cam.tx=camCible.tx; cam.ty=camCible.ty; }
  }
  function versMonde(sx,sy){ return {x:(sx-cam.tx)/cam.s, y:(sy-cam.ty)/cam.s}; }

  /* ---------- Simulation de forces ----------
     Répulsion en O(n²) : fluide jusqu'à ~400 concepts. Au-delà, passer à une
     approximation Barnes-Hut (ou d3-force vendorisé). */
  function tick(t){
    alpha += ((reduit.matches?0:.03)-alpha)*.025;
    var i,j,a,b,dx,dy,d2,d,w,min,p;
    for(i=0;i<noeuds.length;i++){ a=noeuds[i];
      for(j=i+1;j<noeuds.length;j++){ b=noeuds[j];
        dx=b.x-a.x; dy=b.y-a.y; d2=dx*dx+dy*dy||.01;
        if(d2<62500){ w=-160*alpha/d2; a.vx+=dx*w; a.vy+=dy*w; b.vx-=dx*w; b.vy-=dy*w; }
        min=a.r+b.r+10;
        if(d2<min*min){ d=Math.sqrt(d2); p=(min-d)/d*.25; a.x-=dx*p; a.y-=dy*p; b.x+=dx*p; b.y+=dy*p; }
      }
    }
    liens.forEach(function(l){
      dx=l.b.x-l.a.x; dy=l.b.y-l.a.y; d=Math.sqrt(dx*dx+dy*dy)||.01;
      var L = l.croise?150:55, k = l.croise?.08:.4;
      w=(d-L)/d*alpha*k*.5; dx*=w; dy*=w;
      l.b.vx-=dx; l.b.vy-=dy; l.a.vx+=dx; l.a.vy+=dy;
    });
    noeuds.forEach(function(n){
      n.vx += (n.module.ax-n.x)*.05*alpha; n.vy += (n.module.ay-n.y)*.05*alpha;
      if(!reduit.matches){ n.vx += Math.cos(t*.00042+n.ph1)*.02; n.vy += Math.sin(t*.00037+n.ph2)*.02; }
      if(n.fx!=null){ n.x=n.fx; n.y=n.fy; n.vx=n.vy=0; }
      else { n.vx*=.6; n.vy*=.6; n.x+=n.vx; n.y+=n.vy; }
    });
  }

  /* ---------- État actif ---------- */
  function actif(){
    var f = etat.survol || etat.selection;
    if(f) return {set:new Set([f].concat(Array.from(f.voisins))), focus:f};
    if(etat.recherche.length) return {set:new Set(etat.recherche)};
    if(etat.module) return {set:new Set(etat.module.noeuds), module:etat.module};
    return null;
  }
  function emettre(n,dur){
    liens.forEach(function(l){
      if(l.a===n || l.b===n) particules.push({l:l, depuis:n, t0:performance.now(), dur:dur||900, col:n.module.couleur});
    });
  }

  /* ---------- Panneaux ---------- */
  function renderModules(){
    var ul = q('liste-modules'); ul.textContent='';
    MODULES.forEach(function(m){
      var li=document.createElement('li'), b=document.createElement('button');
      b.type='button'; b.className='cs-module'; b.style.setProperty('--cs-c',m.couleur);
      b.setAttribute('aria-pressed', etat.module===m ? 'true':'false');
      var p=document.createElement('span'); p.className='cs-pastille'; p.style.background=m.couleur;
      var nom=document.createElement('span'); nom.className='cs-nom'; nom.textContent=m.nom;
      var pc=document.createElement('span'); pc.className='cs-pct'; pc.textContent=pct(m.maitrise);
      var meta=document.createElement('span'); meta.className='cs-meta';
      meta.textContent = m.noeuds.length+(m.noeuds.length>1?' concepts, ':' concept, ')+(m.nbRevoir===0?'rien':m.nbRevoir)+' à revoir';
      var barre=document.createElement('span'); barre.className='cs-barre';
      var fill=document.createElement('span'); fill.style.width=Math.round(m.maitrise*100)+'%'; fill.style.background=m.couleur;
      barre.appendChild(fill); b.append(p,nom,pc,meta,barre);
      b.addEventListener('click',function(){
        etat.module = etat.module===m ? null : m;
        fermerDetail(true); renderModules();
        cadrer(etat.module ? m.noeuds : noeuds);
      });
      li.appendChild(b); ul.appendChild(li);
    });
    var total = noeuds.filter(function(n){return n.aRevoir;}).length;
    var glob = noeuds.reduce(function(s,n){return s+n.maitrise;},0)/noeuds.length;
    q('resume').textContent = (titre?titre+'. ':'')+'Maîtrise globale '+pct(glob)+', '+total+(total>1?' concepts':' concept')+' à revoir aujourd’hui.';
    canvas.setAttribute('aria-label','Carte de '+noeuds.length+' concepts répartis en '+MODULES.length+' modules. Les modules et les concepts sont aussi accessibles depuis le panneau Modules et la recherche.');
  }

  function afficherDetail(n){
    q('d-pastille').style.background = n.module.couleur;
    q('d-module').textContent = n.module.nom;
    q('d-nom').textContent = n.nom;
    q('d-barre').style.width = Math.round(n.maitrise*100)+'%';
    q('d-barre').style.background = n.module.couleur;
    q('d-maitrise').textContent = n.vu ? pct(n.maitrise) : 'jamais révisé';
    q('d-revision').textContent = texteRevision(n);
    var ul = q('d-voisins'); ul.textContent='';
    Array.from(n.voisins).sort(function(a,b){return (a.module===n.module?0:1)-(b.module===n.module?0:1);}).forEach(function(v){
      var li=document.createElement('li'), b=document.createElement('button'); b.type='button'; b.className='cs-lienbtn';
      var p=document.createElement('span'); p.className='cs-pastille'; p.style.background=v.module.couleur;
      var t=document.createElement('span'); t.textContent = v.module===n.module ? v.nom : v.nom+' ('+v.module.nom+')';
      b.append(p,t); b.addEventListener('click',function(){ selectionner(v); });
      li.appendChild(b); ul.appendChild(li);
    });
    q('detail').hidden = false;
  }
  function selectionner(n){
    if(etat.module && n.module!==etat.module){ etat.module=null; renderModules(); }
    etat.selection=n; afficherDetail(n); centrerSur(n);
    if(!reduit.matches) emettre(n,800);
  }
  function fermerDetail(silencieux){ etat.selection=null; q('detail').hidden=true; if(!silencieux && !etat.module) cadrer(noeuds); }

  q('fermer').addEventListener('click',function(){ fermerDetail(); });
  q('reviser').addEventListener('click',function(){
    var n = etat.selection; if(!n) return;
    if(urlRevision){ window.location.href = urlRevision.split('{id}').join(encodeURIComponent(n.id)); return; }
    var avant = n.maitrise;
    n.vu = true; n.maitrise = Math.min(1,n.maitrise+.25); n.aRevoir = false; n.jours = Math.max(1,Math.round(n.maitrise*18));
    recalculer(); renderModules(); afficherDetail(n);
    if(!reduit.matches){ ondes.push({n:n,t0:performance.now(),col:n.module.couleur}); emettre(n,1100); }
    toast('Concept révisé : maîtrise '+pct(avant)+' → '+pct(n.maitrise));
  });

  var champ = q('recherche');
  champ.addEventListener('input',function(){
    var v = norm(champ.value.trim());
    etat.recherche = v.length>=2 ? noeuds.filter(function(n){return norm(n.nom).indexOf(v)>=0;}) : [];
  });
  champ.addEventListener('keydown',function(e){
    if(e.key==='Enter' && etat.recherche.length){ e.preventDefault(); selectionner(etat.recherche[0]); }
    if(e.key==='Escape'){ champ.value=''; etat.recherche=[]; champ.blur(); }
  });
  q('zoom-plus').addEventListener('click',function(){ zoomer(1.25); });
  q('zoom-moins').addEventListener('click',function(){ zoomer(.8); });
  q('recentrer').addEventListener('click',function(){ etat.module=null; renderModules(); fermerDetail(true); cadrer(noeuds); });
  document.addEventListener('keydown',function(e){
    if(e.key!=='Escape' || document.activeElement===champ) return;
    if(!(document.activeElement===document.body || racine.contains(document.activeElement))) return;
    if(etat.selection) fermerDetail(); else if(etat.module){ etat.module=null; renderModules(); cadrer(noeuds); }
  });

  /* ---------- Pointeur ---------- */
  function local(e){ var r=canvas.getBoundingClientRect(); return {x:e.clientX-r.left, y:e.clientY-r.top}; }
  function toucher(sx,sy){
    var p = versMonde(sx,sy), best=null, bd=Infinity;
    noeuds.forEach(function(n){ var d=Math.hypot(n.x-p.x,n.y-p.y); if(d<n.r*n.zoom+8/cam.s && d<bd){bd=d;best=n;} });
    return best;
  }
  var appui = null;
  canvas.addEventListener('pointerdown',function(e){
    canvas.setPointerCapture(e.pointerId);
    var l = local(e), n = toucher(l.x,l.y);
    appui = {x:l.x,y:l.y,n:n,tx:camCible.tx,ty:camCible.ty,bouge:false};
    if(n){ n.fx=n.x; n.fy=n.y; }
  });
  canvas.addEventListener('pointermove',function(e){
    var l = local(e);
    if(appui){
      var dx=l.x-appui.x, dy=l.y-appui.y;
      if(Math.hypot(dx,dy)>5) appui.bouge = true;
      if(appui.bouge){
        canvas.style.cursor='grabbing';
        if(appui.n){ var p=versMonde(l.x,l.y); appui.n.fx=p.x; appui.n.fy=p.y; alpha=Math.max(alpha,.25); }
        else { camCible.tx=appui.tx+dx; camCible.ty=appui.ty+dy; cam.tx=camCible.tx; cam.ty=camCible.ty; }
      }
      return;
    }
    etat.survol = toucher(l.x,l.y);
    canvas.style.cursor = etat.survol ? 'pointer' : 'grab';
  });
  canvas.addEventListener('pointerup',function(){
    if(!appui) return;
    var a = appui; appui = null;
    if(a.n){ a.n.fx=null; a.n.fy=null; }
    canvas.style.cursor = 'grab';
    if(a.bouge) return;
    if(a.n) selectionner(a.n);
    else if(etat.selection) fermerDetail();
  });
  canvas.addEventListener('pointerleave',function(){ if(!appui) etat.survol=null; });
  canvas.addEventListener('wheel',function(e){
    e.preventDefault(); var l = local(e);
    zoomer(Math.exp(-e.deltaY*.0015),l.x,l.y);
  },{passive:false});

  /* ---------- Dessin ---------- */
  function controle(l){
    var mx=(l.a.x+l.b.x)/2, my=(l.a.y+l.b.y)/2, dx=l.b.x-l.a.x, dy=l.b.y-l.a.y, c=l.croise?.22:.12;
    return {x:mx-dy*c*l.sens, y:my+dx*c*l.sens};
  }
  function surCourbe(l,t,inverse){
    var c=controle(l), a=inverse?l.b:l.a, b=inverse?l.a:l.b, u=1-t;
    return {x:u*u*a.x+2*u*t*c.x+t*t*b.x, y:u*u*a.y+2*u*t*c.y+t*t*b.y};
  }

  function dessiner(t){
    var intro = reduit.matches ? 1 : Math.min(1,(t-debut)/900);
    var ac = actif(), f = ac && ac.focus;
    var lent = reduit.matches ? 1 : .14, s = cam.s, now = performance.now();

    noeuds.forEach(function(n){
      var cv = (!ac || ac.set.has(n)) ? 1 : .16;
      n.vis += (cv*intro-n.vis)*lent;
      var cz = n===f ? 1.3 : (f && f.voisins.has(n) ? 1.1 : 1);
      n.zoom += (cz-n.zoom)*lent;
      n.mAff += ((n.vu?n.maitrise:0)*intro-n.mAff)*(reduit.matches?1:.06);
    });

    ctx.setTransform(DPR,0,0,DPR,0,0);
    ctx.fillStyle = T.fond; ctx.fillRect(0,0,W,H);
    ctx.setTransform(DPR*s,0,0,DPR*s,DPR*cam.tx,DPR*cam.ty);

    // Territoires
    var aTerr = T.sombre ? .14 : .11;
    noeuds.forEach(function(n){
      var dim = ac && !(ac.module===n.module || ac.set.has(n)) ? .35 : 1;
      var g = ctx.createRadialGradient(n.x,n.y,0,n.x,n.y,80);
      g.addColorStop(0,rgba(n.module.couleur,aTerr*dim*intro)); g.addColorStop(1,rgba(n.module.couleur,0));
      ctx.fillStyle=g; ctx.beginPath(); ctx.arc(n.x,n.y,80,0,TAU); ctx.fill();
    });

    // Liens
    ctx.lineCap='round';
    liens.forEach(function(l){
      var c = controle(l), fort = f && (l.a===f || l.b===f), vis = Math.min(l.a.vis,l.b.vis);
      ctx.beginPath(); ctx.moveTo(l.a.x,l.a.y); ctx.quadraticCurveTo(c.x,c.y,l.b.x,l.b.y);
      if(fort){ ctx.strokeStyle=rgba(f.module.couleur,.85); ctx.lineWidth=2.2/s; }
      else { ctx.strokeStyle=T.lien; ctx.globalAlpha=(l.croise?.45:.7)*vis; ctx.lineWidth=(l.croise?1:1.3)/s; if(l.croise) ctx.setLineDash([4/s,5/s]); }
      ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha=1;
    });

    // Flux le long des liens au survol
    if(f && etat.survol===f && !reduit.matches && now-dernierFlux>750){ emettre(f,1000); dernierFlux=now; }
    particules = particules.filter(function(p){
      var k = (now-p.t0)/p.dur; if(k>=1) return false;
      var e = k<.5 ? 2*k*k : 1-Math.pow(-2*k+2,2)/2, pt = surCourbe(p.l,e,p.l.b===p.depuis);
      ctx.globalAlpha = Math.sin(k*Math.PI); ctx.fillStyle = p.col;
      ctx.beginPath(); ctx.arc(pt.x,pt.y,3/s,0,TAU); ctx.fill(); ctx.globalAlpha = 1;
      return true;
    });

    // Ondes de révision
    ondes = ondes.filter(function(o){
      var k=(now-o.t0)/1100; if(k>=1) return false;
      ctx.strokeStyle=rgba(o.col,(1-k)*.7); ctx.lineWidth=2.5/s;
      ctx.beginPath(); ctx.arc(o.n.x,o.n.y,o.n.r+k*55,0,TAU); ctx.stroke();
      return true;
    });

    // Concepts
    noeuds.forEach(function(n){
      var r = n.r*n.zoom, col = n.module.couleur;
      ctx.globalAlpha = n.vis;
      if(n.aRevoir){
        var p = reduit.matches ? .6 : .5+.5*Math.sin(t/520+n.ph1*10);
        ctx.beginPath(); ctx.arc(n.x,n.y,r+4+p*4,0,TAU);
        ctx.fillStyle=rgba(col,.06+.08*p); ctx.fill();
        ctx.strokeStyle=rgba(col,.2+.45*p); ctx.lineWidth=2/s; ctx.stroke();
      }
      ctx.beginPath(); ctx.arc(n.x,n.y,r,0,TAU); ctx.fillStyle=T.panneau; ctx.fill();
      if(!n.vu){
        ctx.setLineDash([3/s,3/s]); ctx.strokeStyle=T.eteint; ctx.lineWidth=1.6/s; ctx.stroke(); ctx.setLineDash([]);
      }else{
        ctx.strokeStyle=rgba(col,.2); ctx.lineWidth=3.2/s; ctx.stroke();
        ctx.beginPath(); ctx.arc(n.x,n.y,r,-Math.PI/2,-Math.PI/2+TAU*Math.max(.001,n.mAff));
        ctx.strokeStyle=col; ctx.lineWidth=3.2/s; ctx.stroke();
        ctx.beginPath(); ctx.arc(n.x,n.y,r*.48,0,TAU); ctx.fillStyle=rgba(col,.15+.85*n.mAff); ctx.fill();
      }
      if(n===etat.selection){ ctx.beginPath(); ctx.arc(n.x,n.y,r+5/s+2,0,TAU); ctx.strokeStyle=T.texte; ctx.lineWidth=1.8/s; ctx.stroke(); }
      ctx.globalAlpha = 1;
    });

    // Textes en espace écran, sans chevauchement
    ctx.setTransform(DPR,0,0,DPR,0,0);
    ctx.lineJoin='round'; ctx.textBaseline='middle';
    var boites = [];
    noeuds.forEach(function(n){
      if(ac && !ac.set.has(n)) return;
      var rr=n.r*n.zoom*s+2, sx=n.x*s+cam.tx, sy=n.y*s+cam.ty;
      boites.push({x:sx-rr,y:sy-rr,w:rr*2,h:rr*2,n:n});
    });
    function libre(b,soi){ return !boites.some(function(o){ return o.n!==soi && b.x<o.x+o.w && b.x+b.w>o.x && b.y<o.y+o.h && b.y+b.h>o.y; }); }

    MODULES.forEach(function(m){
      var cx=0, top=Infinity, vis=0;
      m.noeuds.forEach(function(n){ cx+=n.x; top=Math.min(top,n.y-n.r); vis=Math.max(vis,n.vis); });
      cx/=m.noeuds.length;
      var sx=cx*s+cam.tx, sy=(top-22)*s+cam.ty;
      ctx.font='700 15px '+T.police;
      var w=ctx.measureText(m.nom).width;
      boites.push({x:sx-w/2-4,y:sy-11,w:w+8,h:22});
      ctx.globalAlpha = Math.max(.3,vis)*intro;
      ctx.lineWidth=5; ctx.strokeStyle=T.fond; ctx.strokeText(m.nom,sx-w/2,sy);
      ctx.fillStyle = m.couleur; ctx.fillText(m.nom,sx-w/2,sy);
    });

    function prio(n){
      if(n===etat.selection) return 1000;
      if(n===etat.survol) return 900;
      if(f && f.voisins.has(n)) return 500;
      if(etat.recherche.indexOf(n)>=0) return 400;
      if(ac && ac.module===n.module) return 200+n.r;
      return n.r*2+(n.aRevoir?12:0);
    }
    noeuds.slice().sort(function(a,b){return prio(b)-prio(a);}).forEach(function(n){
      var p = prio(n);
      if(ac && !ac.set.has(n)) return;
      if(s<.6 && p<200) return;
      var sx=n.x*s+cam.tx, sy=n.y*s+cam.ty;
      if(sx<-50||sx>W+50||sy<-20||sy>H+20) return;
      ctx.font=(p>=900?'700 14px ':'500 13px ')+T.police;
      var w=ctx.measureText(n.nom).width, x=sx+n.r*n.zoom*s+7, b={x:x-3,y:sy-9,w:w+6,h:18};
      if(p<900 && !libre(b,n)) return;
      boites.push(b);
      ctx.globalAlpha = Math.min(1,n.vis*1.15);
      ctx.lineWidth=4; ctx.strokeStyle=T.fond; ctx.strokeText(n.nom,x,sy);
      ctx.fillStyle = n.vu ? T.texte : T['texte-doux']; ctx.fillText(n.nom,x,sy);
    });
    ctx.globalAlpha = 1;
  }

  /* ---------- Boucle ---------- */
  function boucle(t){
    if(!racine.isConnected){ enMarche=false; return; }
    if(debut===null) debut = t;
    if(!reduit.matches || alpha>.004) tick(t);
    if(!recadre && t-debut>1400){ recadre=true; if(!etat.selection && !etat.module) cadrer(noeuds); }
    var k = reduit.matches ? 1 : .12;
    cam.s += (camCible.s-cam.s)*k; cam.tx += (camCible.tx-cam.tx)*k; cam.ty += (camCible.ty-cam.ty)*k;
    dessiner(t);
    requestAnimationFrame(boucle);
  }

  charger();
}

function monter(){
  document.querySelectorAll('[data-carte-savoir]').forEach(function(el){
    if(el.__carteSavoir) return; el.__carteSavoir = true; CarteSavoir(el);
  });
}
if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',monter); else monter();
window.CarteSavoir = {monter:monter};
})();
