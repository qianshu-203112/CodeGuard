"""
可视化模块 - 生成代码图谱 HTML 页面

用法:
  python -m code_guard.viz <项目路径> -o graph.html
"""
import os
import sys
import json
import html
from pathlib import Path
from typing import Dict, Optional

_src = Path(__file__).parent.parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from code_guard.parser.ast_parser import parse_project_multilang
from code_guard.graph.code_graph import CodeGraph
from code_guard.analyzer import ModuleDependencyAnalyzer

VIZ_HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>CodeGuard</title>
<script src="https://cdn.bootcdn.net/ajax/libs/d3/7.8.5/d3.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0d1117;color:#c9d1d9}
.tbar{display:flex;align-items:center;height:44px;background:#161b22;border-bottom:1px solid #30363d;padding:0 16px;gap:8px}
.tbar h1{font-size:15px;color:#58a6ff}
.tbtn{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:4px 12px;border-radius:6px;cursor:pointer;font-size:12px}
.tbtn.on{background:#1f6feb;border-color:#1f6feb;color:#fff}
.hint{font-size:11px;color:#8b949e;margin-left:auto}
.wrap{display:flex;height:calc(100vh - 44px)}
.cv{flex:1;position:relative;background:#0d1117}
.cv svg{width:100%;height:100%;display:block}
.sd{width:280px;background:#161b22;border-left:1px solid #30363d;overflow-y:auto;padding:12px;font-size:13px;flex-shrink:0}
.sd h3{color:#58a6ff;margin-bottom:10px;font-size:14px;border-bottom:1px solid #30363d;padding-bottom:6px}
.it{padding:6px 10px;border-radius:4px;cursor:pointer;margin-bottom:3px}
.it:hover{background:#21262d}
.it.sel{background:#1f6feb}
.it .n{color:#c9d1d9}
.it .m{color:#8b949e;font-size:11px;margin-top:2px}
.it.core{border-left:3px solid #d29922;background:rgba(210,153,34,0.05)}
.it.mid{border-left:3px solid #58a6ff;background:rgba(88,166,255,0.05)}
.it.leaf{border-left:3px solid #8b949e;background:rgba(139,148,158,0.05)}
.it.leaf .n,.it.leaf .m{color:#8b949e!important}
.it.sel{background:#1f6feb!important;border-left-color:#fff!important}
.st{position:fixed;bottom:12px;left:12px;background:rgba(22,27,34,0.92);border:1px solid #30363d;border-radius:8px;padding:10px 14px;font-size:11px;z-index:5;display:flex;gap:4px;align-items:center}
.st span{margin-right:6px}
.st .num{color:#58a6ff;font-weight:600}
.ep{padding:60px 20px;text-align:center;color:#8b949e}
.ep h2{color:#58a6ff;font-size:18px;margin-bottom:10px}
.legend-dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:3px;vertical-align:middle}
</style>
</head>
<body>
<div class="tbar"><h1>CodeGuard</h1><button class="tbtn on" id="bm" onclick="sw('m')">模块依赖</button><button class="tbtn" id="bc" onclick="sw('c')">调用链</button><span class="hint">模块依赖:悬停看import | 调用链:搜索函数名看调用关系</span></div>
<div class="wrap"><div class="cv" id="cv"></div><div class="sd" id="sd"><div class="ep"><h2>欢迎</h2></div></div></div>
<div class="st" id="st"></div>
<script>
var MD=__MODULE_JSON__;var CD=__CALL_JSON__;var sim,svg,g,W,H;
function sw(v){
  document.getElementById('bm').className='tbtn'+(v=='m'?' on':'');
  document.getElementById('bc').className='tbtn'+(v=='c'?' on':'');
  document.getElementById('cv').innerHTML='';document.getElementById('sd').innerHTML='';document.getElementById('st').innerHTML='';
  if(sim){sim.stop();}
  if(v=='m')dm();else dc();
}
function su(){
  var c=document.getElementById('cv');
  W=c.clientWidth||900;H=c.clientHeight||600;
  svg=d3.select('#cv').append('svg').attr('viewBox','0 0 '+W+' '+H);
  // 箭头
  svg.append('defs').append('marker').attr('id','ar').attr('viewBox','0 -5 10 10').attr('refX',25).attr('refY',0).attr('markerWidth',8).attr('markerHeight',8).attr('orient','auto').append('path').attr('d','M0,-5L10,0L0,5').attr('fill','#58a6ff').attr('opacity',0.6);
  g=svg.append('g');
  svg.call(d3.zoom().scaleExtent([0.2,5]).on('zoom',function(e){g.attr('transform',e.transform);}));
  svg.call(d3.zoom().transform,d3.zoomIdentity.translate(60,60));
}
function sel(el){document.querySelectorAll('.it.sel').forEach(function(x){x.classList.remove('sel');});if(el)el.classList.add('sel');}
function hl(id){
  g.selectAll('.nc').attr('stroke','#30363d').attr('stroke-width',2);
  g.selectAll('.nt').attr('font-weight','normal');
  g.selectAll('g').filter(function(d){return d&&d.id===id;}).select('.nc').attr('stroke','#f0883e').attr('stroke-width',3);
  g.selectAll('g').filter(function(d){return d&&d.id===id;}).select('.nt').attr('font-weight','bold');}
function dm(){
  var d=MD;
  if(!d||!d.dependencies||!d.dependencies.length){document.getElementById('cv').innerHTML='<div class="ep"><h2>模块依赖</h2><p>所有文件在同一目录</p></div>';document.getElementById('sd').innerHTML='<h3>提示</h3><div class="ep"><p>切换调用链</p></div>';return;}
  su();var ns=[],ls=[],allMods=new Set();
  d.dependencies.forEach(function(x){allMods.add(x.from);allMods.add(x.to);});
  (d.orphan_modules||[]).forEach(function(m){allMods.add(m);});
  // 如果还有模块在 modules 里但不在依赖中，也加上
  if(d.modules)Object.keys(d.modules).forEach(function(m){allMods.add(m);});
  allMods.forEach(function(m){var c=d.core_modules&&d.core_modules.find(function(x){return x.name===m;});ns.push({id:m,cc:c?c.depended_by_count:0,cy:d.circular_deps&&d.circular_deps.some(function(x){return x.includes(m);})});});
  d.dependencies.forEach(function(x){ls.push({s:x.from,t:x.to,imports:x.imports||[]});});
  // 分层赋值
  var maxDep=ns.reduce(function(m,n){return Math.max(m,n.cc);},0);
  ns.forEach(function(n){
    if(n.cc>=maxDep*0.5) n._layer=0;
    else if(n.cc>0||d.dependencies.some(function(x){return x.from===n.id;})) n._layer=1;
    else n._layer=2;
  });
  ns.sort(function(a,b){return a._layer-b._layer||b.cc-a.cc;});
  document.getElementById('sd').innerHTML='<h3>模块</h3>'+ns.map(function(n){
    var cls='it',bg='';
    if(n.cy){cls='it cycle';bg=' 循环';}
    else if(n._layer===0){cls='it core';bg=' 核心('+n.cc+')';}
    else if(n._layer===1){cls='it mid';bg=' 中层';}
    else {cls='it leaf';bg=' 叶子';}
    return '<div class="'+cls+'" onclick="sel(this);hl(\\''+n.id+'\\')"><div class="n">'+n.id+'/</div><div class="m">'+bg+'</div></div>';}).join('');
  document.getElementById('st').innerHTML=d.stats?'<span><b class="num">'+d.stats.total_modules+'</b> 模块</span><span><b class="num">'+d.stats.total_dependencies+'</b> 依赖</span>':'';
  var l=g.append('g').selectAll('line').data(ls).join('line').attr('marker-end','url(#ar)').attr('stroke','#58a6ff').attr('stroke-width',2).attr('opacity',0.6);
  l.append('title').text(function(d){
    var items=(d.imports||[]).map(function(x){var f=x.file.split(/[\\\\/]/).pop();return f+': import '+x.source;});
    return d.s+' \\u2192 '+d.t+'\\n'+(items.length?items.slice(0,5).join('\\n')+(items.length>5?'\\n...还有'+items.length-5+'个':'') :'');
  });
  var nd=g.append('g').selectAll('g').data(ns).join('g').call(d3.drag().on('start',function(e,d){if(!e.active)sim.alphaTarget(0.1).restart();d.fx=e.x;d.fy=e.y;}).on('drag',function(e,d){d.fx=e.x;d.fy=e.y;}).on('end',function(e,d){if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}));
  nd.append('circle').attr('class','nc').attr('r',function(d){return 16+d.cc*3;}).attr('fill',function(d){
    if(d.cy)return '#f85149';
    if(d._layer===0)return '#d29922';   // 核心层 金色
    if(d._layer===1)return '#58a6ff';   // 中间层 蓝色
    return '#8b949e';                   // 叶子层 灰色
  }).attr('stroke','#fff').attr('stroke-width',2);
  nd.append('text').attr('class','nt').attr('dx',function(d){return 20+d.cc*3;}).attr('dy',5).text(function(d){return d.id;}).attr('fill','#c9d1d9').style('font-size','13px').style('font-weight','500');
  // 分层布局
  var layerGroups=[[],[],[]];
  ns.sort(function(a,b){return a._layer-b._layer||b.cc-a.cc;}).forEach(function(n){layerGroups[n._layer].push(n);});
  var topMargin=80,rowGap=(H-160)/Math.max(1,layerGroups.filter(function(g){return g.length;}).length);
  var curY=topMargin;
  layerGroups.forEach(function(grp){
    if(!grp.length)return;
    var gw=W-120,gap=gw/(grp.length+1);
    grp.forEach(function(d,i){d.x=60+gap*(i+1);d.y=curY;});
    curY+=rowGap;
  });
  var lineMap={};ns.forEach(function(n){lineMap[n.id]=n;});
  l.attr('x1',function(d){return lineMap[d.s]?lineMap[d.s].x:W/2;}).attr('y1',function(d){return lineMap[d.s]?lineMap[d.s].y:H/2;}).attr('x2',function(d){return lineMap[d.t]?lineMap[d.t].x:W/2;}).attr('y2',function(d){return lineMap[d.t]?lineMap[d.t].y:H/2;});
  nd.attr('transform',function(d){return 'translate('+d.x+','+d.y+')';});
  sim=d3.forceSimulation(ns).alpha(0).on('tick',function(){});}
function dc(){
  var d=CD;if(!d||!d.nodes||!d.nodes.length){document.getElementById('cv').innerHTML='<div class="ep"><h2>调用链</h2><p>未找到调用关系</p></div>';return;}
  su();
  window._cd=d;window._nmap={};d.nodes.forEach(function(n){window._nmap[n.id]=n;});
  window._callers={};window._callees={};
  (d.links||[]).forEach(function(l){
    if(!window._callers[l.target])window._callers[l.target]=[];
    window._callers[l.target].push(l.source);
    if(!window._callees[l.source])window._callees[l.source]=[];
    window._callees[l.source].push(l.target);
  });
  var total=d.nodes.length;
  document.getElementById('sd').innerHTML='<h3>搜索函数<span style="font-size:11px;color:#8b949e;font-weight:normal;margin-left:6px">共'+total+'个</span></h3><input id="fs" oninput="sf()" placeholder="输入函数名..." style="width:100%;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:6px 8px;border-radius:4px;font-size:12px;margin-bottom:8px"><div id="fl"></div>';
  document.getElementById('st').innerHTML='<span><span class="legend-dot" style="background:#2ea043"></span>选中</span><span><span class="legend-dot" style="background:#f85149"></span>调用者</span><span><span class="legend-dot" style="background:#58a6ff"></span>被调用者</span>';
  sf();}
function sf(){
  var d=window._cd,q=document.getElementById('fs').value.toLowerCase().trim();
  var matched=d.nodes.filter(function(n){return n.id.toLowerCase().indexOf(q)>=0;}).sort(function(a,b){return(b.calls+b.called_by)-(a.calls+a.called_by);});
  document.getElementById('fl').innerHTML=matched.map(function(n){
    return '<div class="it" data-id="'+n.id+'" onclick="sel(this);showFunc(\\''+n.id+'\\')"><div class="n">'+(n.id.length>30?n.id.slice(0,28)+'..':n.id)+'</div><div class="m">'+(n.file||'')+'</div></div>';}).join('');
  if(matched.length)showFunc(matched[0].id);
}
function showFunc(id){
  var d=window._cd,nm=window._nmap,cr=window._callers,cl=window._callees;
  if(!d||!id||!nm[id])return;
  d3.select('#cv svg').remove();su();g=svg.select('g');
  var center=nm[id],callers=(cr[id]||[]).filter(function(x){return nm[x];}),callees=(cl[id]||[]).filter(function(x){return nm[x];});
  var nodes=[center];
  callers.forEach(function(x){if(!nodes.some(function(n){return n.id===x;}))nodes.push(nm[x]);});
  callees.forEach(function(x){if(!nodes.some(function(n){return n.id===x;}))nodes.push(nm[x]);});
  var links=[];
  callers.forEach(function(x){links.push({s:x,t:id});});
  callees.forEach(function(x){links.push({s:id,t:x});});
  if(!nodes.length)return;
  var R=Math.min(W,H)*0.35;
  // 调用者在左半圆，被调用者在右半圆
  function arcLayout(items,side){
    var total=items.length;
    if(total===0)return;
    var arcR=R*(0.5+total*0.06); // 动态半径
    arcR=Math.min(arcR,R*0.9);
    var startAngle=Math.PI*0.6,endAngle=Math.PI*0.4; // 左半圆 0.6π~1.4π, 右半圆 -0.4π~0.4π
    if(side==='right'){startAngle=-Math.PI*0.4;endAngle=Math.PI*0.4;}
    else{startAngle=Math.PI*0.6;endAngle=Math.PI*1.4;}
    items.forEach(function(fid,j){
      var t=total>1?j/(total-1):0.5;
      var angle=startAngle+(endAngle-startAngle)*t;
      var node=nm[fid];
      if(node){node.x=W/2+Math.cos(angle)*arcR;node.y=H/2+Math.sin(angle)*arcR;node._role=side==='right'?'callee':'caller';}
    });
  }
  arcLayout(callers,'left');
  arcLayout(callees,'right');
  // 选中函数居中
  center.x=W/2;center.y=H/2;center._role='center';
  var ll=g.append('g').selectAll('line').data(links).join('line').attr('marker-end','url(#ar)').attr('stroke','#58a6ff').attr('stroke-width',1.5).attr('opacity',0.5);
  var nn=g.append('g').selectAll('g').data(nodes).join('g').call(d3.drag().on('start',function(e,d){d.fx=e.x;d.fy=e.y;}).on('drag',function(e,d){d.fx=e.x;d.fy=e.y;}).on('end',function(e,d){d.fx=null;d.fy=null;}));
  nn.append('circle').attr('class','nc').attr('r',function(d){return d.id===id?18:6+Math.min(10,(d.calls||0)+(d.called_by||0));}).attr('fill',function(d){return d.id===id?'#2ea043':d._role==='caller'?'#f85149':'#58a6ff';}).attr('stroke','#fff').attr('stroke-width',2);
  nn.append('text').attr('class','nt').text(function(d){var s=d.id.split('.').pop();return s.length>12?s.slice(0,10)+'..':s;}).attr('dx',function(d){return d.id===id?22:14;}).attr('dy',4).attr('fill','#c9d1d9').style('font-size',d.id===id?'13px':'11px').style('font-weight',d.id===id?'bold':'normal');
  // 高亮选中的侧栏项
  document.querySelectorAll('#fl .it').forEach(function(el){el.classList.remove('sel');});
  var target=document.querySelector('#fl .it[data-id="'+id+'"]');
  if(target)target.classList.add('sel');
  var nodePos={};nodes.forEach(function(n){nodePos[n.id]=n;});
  ll.attr('x1',function(d){return nodePos[d.s]?nodePos[d.s].x:W/2;}).attr('y1',function(d){return nodePos[d.s]?nodePos[d.s].y:H/2;}).attr('x2',function(d){return nodePos[d.t]?nodePos[d.t].x:W/2;}).attr('y2',function(d){return nodePos[d.t]?nodePos[d.t].y:H/2;});
  nn.attr('transform',function(d){return 'translate('+d.x+','+d.y+')';});}
window.onload=function(){sw('m');};
</script>
</body>
</html>'''


def _get_callgraph_data(graph: CodeGraph) -> Dict:
    """提取调用链数据。

    节点 id 用唯一键 `qualified_name@文件basename`：跨文件同名函数（如两个
    文件的 _http_post）不再互相覆盖，右侧导航栏可区分。空 qualified_name
    的函数（匿名回调等）不生成节点，避免一堆空白圆堆叠。
    """
    conn = graph.conn
    funcs = conn.execute("""
        SELECT f.qualified_name, fl.path, f.name,
               (SELECT COUNT(*) FROM call_edges ce WHERE ce.caller_func_id = f.id) as out_count,
               (SELECT COUNT(*) FROM call_edges ce2 WHERE ce2.callee_name = f.name
                OR ce2.callee_name LIKE '%' || f.name) as in_count
        FROM functions f
        JOIN files fl ON f.file_id = fl.id
        ORDER BY (out_count + in_count) DESC
    """).fetchall()

    # id -> (qualified_name, simple_name)，同一 qualified_name 多个文件时各自独立节点
    simple_map = {}          # simple_name -> [qualified_name, ...]（保持多个）
    qname_to_file = {}       # qualified_name -> 已使用的 id（同 qname 多文件时加后缀）
    id_meta = {}             # id -> 元信息

    for qname, path, name, out_c, in_c in funcs:
        if not qname:
            continue  # 空限定名（匿名回调等）不入图
        simple = qname.split(".")[-1]
        base = Path(path).name
        # 唯一 id：qualified_name 同名的多文件函数用 @文件区分
        nid = qname
        if nid in id_meta:
            nid = f"{qname}@{base}"
        id_meta[nid] = {"qname": qname, "simple": simple, "file": base,
                        "calls": out_c, "called_by": in_c}
        if simple not in simple_map:
            simple_map[simple] = []
        simple_map[simple].append(nid)
        qname_to_file[qname] = base

    nodes = []
    node_set = set()
    for nid in list(id_meta.keys())[:200]:
        m = id_meta[nid]
        node_set.add(nid)
        nodes.append({"id": nid, "file": m["file"], "calls": m["calls"],
                      "called_by": m["called_by"]})

    edges = conn.execute("""
        SELECT f1.qualified_name, ce.callee_name, fl.path
        FROM call_edges ce
        JOIN functions f1 ON ce.caller_func_id = f1.id
        JOIN files fl ON f1.file_id = fl.id
        WHERE LENGTH(ce.callee_name) < 60
        LIMIT 500
    """).fetchall()

    links = []
    seen = set()
    for caller_qname, callee_name, caller_path in edges:
        # caller 匹配节点：按 qualified_name + 调用点所在文件定位
        caller_id = caller_qname
        if caller_id not in node_set and f"{caller_qname}@{Path(caller_path).name}" in node_set:
            caller_id = f"{caller_qname}@{Path(caller_path).name}"
        if caller_id not in node_set:
            continue
        callee_simple = callee_name.split(".")[-1]
        # callee 定位：优先精确 qualified_name，其次按简单名，可能多个则都连
        candidates = []
        for nid in node_set:
            m = id_meta[nid]
            if m["qname"] == callee_name or (m["qname"].endswith("." + callee_name)):
                candidates.append(nid)
            elif callee_simple and m["simple"] == callee_simple and \
                    (callee_name == callee_simple or m["qname"] == callee_name):
                candidates.append(nid)
        for target in candidates:
            if target == caller_id:
                continue
            key = caller_id + "->" + target
            if key not in seen:
                seen.add(key)
                links.append({"source": caller_id, "target": target})
        # 简单名候选未被上面的精确匹配覆盖时，补充连接
        if not candidates:
            for nid in simple_map.get(callee_simple, []):
                if nid != caller_id and nid in node_set:
                    key = caller_id + "->" + nid
                    if key not in seen:
                        seen.add(key)
                        links.append({"source": caller_id, "target": nid})

    return {"nodes": nodes, "links": links}


def generate_html(project_path: str, output_path: str = "code_graph.html",
                  results: Optional[dict] = None):
    """生成可视化 HTML 文件

    Args:
        results: 可选的预解析结果；不传则内部解析（CLI 直接调用时用）。
                 传入可避免 Web 服务重复解析项目。
    """
    if results is None:
        print(f"正在解析 {project_path} ...")
        results = parse_project_multilang(project_path)
    graph = CodeGraph()
    graph.load_project(results)

    analyzer = ModuleDependencyAnalyzer(graph, results)
    module_data = analyzer.analyze()
    call_data = _get_callgraph_data(graph)

    html = VIZ_HTML
    html = html.replace("__MODULE_JSON__", json.dumps(module_data, ensure_ascii=False))
    html = html.replace("__CALL_JSON__", json.dumps(call_data, ensure_ascii=False))

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    stats = graph.get_stats()
    print(f"已完成: {output_path}")
    print(f"{stats['files']} 文件, {stats['functions']} 函数, {stats['calls']} 调用边")
    print(f"{module_data['stats']['total_modules']} 模块, {module_data['stats']['total_dependencies']} 依赖")

    graph.close()
    return output_path


# ── 版本对比报告 ──

_DIFF_COLORS = {"added": "#2ea043", "removed": "#f85149", "modified": "#d29922"}


def _diff_section(title: str, items: list, status: str) -> str:
    """渲染一个 diff 列表区。items 为 dict 或 str 列表，按 key 展示。"""
    if not items:
        return ""
    color = _DIFF_COLORS[status]
    rows = []
    for it in items[:200]:
        if isinstance(it, str):
            name, detail = it, ""
        elif it.get("new"):   # 修改类条目：func + 行范围变化
            name = it.get("func") or it.get("class") or ""
            detail = (f"{it.get('file','')} "
                      f"[{it['old'][0]}-{it['old'][1]} → {it['new'][0]}-{it['new'][1]}]")
        elif it.get("func"):
            name, detail = it["func"], f"{it.get('file','')}:{it.get('line','')}"
        elif it.get("class"):
            name, detail = it["class"], it.get("file", "")
        else:
            name, detail = str(it), ""
        rows.append(
            f'<li><span class="tag" style="background:{color}">{status}</span>'
            f'<code>{html.escape(str(name))}</code>'
            f'<span class="det">{html.escape(str(detail))}</span></li>')
    return f'<h3>{title} <span class="cnt">{len(items)}</span></h3><ul>{"".join(rows)}</ul>'


def generate_diff_html(diff: dict, output_path: str = "diff_report.html"):
    """生成版本对比报告 HTML（自包含、无外部依赖，浏览器直接打开）。

    统计卡片 + 新增(绿)/删除(红)/修改(橙)列表 + 变更影响区。
    """
    s = diff["stats"]
    cards = [
        ("新增文件", s["added_files"], "added"),
        ("删除文件", s["removed_files"], "removed"),
        ("修改文件", s["modified_files"], "modified"),
        ("新增函数", s["added_functions"], "added"),
        ("删除函数", s["removed_functions"], "removed"),
        ("修改函数", s["modified_functions"], "modified"),
    ]
    card_html = "".join(
        f'<div class="card"><div class="num" style="color:{_DIFF_COLORS[st]}">{n}</div>'
        f'<div class="lbl">{t}</div></div>' for t, n, st in cards)

    callee_html = ""
    for label, key, tag in (("新增调用关系", "added", "added"),
                            ("移除调用关系", "removed", "removed")):
        items = diff["callees"][key]
        if not items:
            continue
        rows = [f'<li><span class="tag" style="background:{_DIFF_COLORS[tag]}">{tag}</span>'
                f'<code>{html.escape(i["func"])}</code> → '
                f'{", ".join("<code>" + html.escape(c) + "</code>" for c in i["callees"])}</li>'
                for i in items[:200]]
        callee_html += f'<h3>{label} <span class="cnt">{len(items)}</span></h3><ul>{"".join(rows)}</ul>'

    impact_html = ""
    if diff["impact"]["count"]:
        impact_html = (
            f'<h3>变更影响 <span class="cnt">{diff["impact"]["count"]}</span></h3>'
            f'<p class="det">修改下列函数会波及到的调用方 / 文件：</p>'
            f'<ul>'
            + "".join(f'<li><span class="tag" style="background:#58a6ff">波及</span>'
                      f'<code>{html.escape(f)}</code></li>'
                      for f in diff["impact"]["affected_functions"][:200])
            + "</ul>")

    page = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>CodeGuard 版本对比 {html.escape(diff['base'])} → {html.escape(diff['head'])}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0d1117;color:#c9d1d9;padding:24px;max-width:960px;margin:0 auto}}
h1{{font-size:18px;color:#58a6ff;margin-bottom:6px}}
.sub{{color:#8b949e;font-size:13px;margin-bottom:20px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:24px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;text-align:center}}
.card .num{{font-size:22px;font-weight:700}}
.card .lbl{{font-size:12px;color:#8b949e;margin-top:2px}}
h3{{color:#58a6ff;font-size:14px;margin:20px 0 8px;border-bottom:1px solid #30363d;padding-bottom:6px}}
h3 .cnt{{color:#8b949e;font-size:12px;font-weight:normal}}
ul{{list-style:none}}
li{{padding:5px 0;font-size:13px;border-bottom:1px solid #21262d}}
.tag{{display:inline-block;font-size:11px;color:#fff;padding:1px 7px;border-radius:10px;margin-right:8px;vertical-align:1px}}
code{{background:#21262d;border-radius:4px;padding:1px 5px;font-size:12px;color:#e6edf3}}
.det{{color:#8b949e;font-size:12px;margin-left:8px}}
</style></head><body>
<h1>CodeGuard 版本图谱对比</h1>
<div class="sub">{html.escape(diff['base'])} → {html.escape(diff['head'])} ｜ 文件 {s['base_files']} → {s['head_files']} ｜ 对比 {s['changed_functions']} 个 changed 函数</div>
<div class="cards">{card_html}</div>
{_diff_section("新增文件", diff["files"]["added"], "added")}
{_diff_section("删除文件", diff["files"]["removed"], "removed")}
{_diff_section("修改文件", diff["files"]["modified"], "modified")}
{_diff_section("新增函数", diff["functions"]["added"], "added")}
{_diff_section("删除函数", diff["functions"]["removed"], "removed")}
{_diff_section("修改函数", diff["functions"]["modified"], "modified")}
{_diff_section("新增类", diff["classes"]["added"], "added")}
{_diff_section("删除类", diff["classes"]["removed"], "removed")}
{_diff_section("修改类", diff["classes"]["modified"], "modified")}
{callee_html}
{impact_html}
</body></html>"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"已完成: {output_path}")
    return output_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CodeGuard 可视化")
    parser.add_argument("path", help="项目路径")
    parser.add_argument("-o", "--output", default="code_graph.html", help="输出 HTML 路径")
    args = parser.parse_args()
    generate_html(args.path, args.output)
