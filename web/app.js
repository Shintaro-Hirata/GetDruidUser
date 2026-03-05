// web/app.js - lightweight UI/Plotly glue
(function(){
  const $ = (id)=>document.getElementById(id);
  const parseISO = (s)=> (s ? new Date(s) : null);

  async function fetchScatter() {
    const vehicle_id = $('vehicle_id').value;
    const start_ts = $('start_ts').value;
    const end_ts = $('end_ts').value;
    const limit = $('limit').value || 200;
    const backend = $('backend').value || 'bigquery';
    const project = $('project').value || 't2-integration';

    const qs = new URLSearchParams({
      vehicle_id, start_ts, end_ts, limit, backend, project
    });
    const url = `/api/scatter?${qs.toString()}`;
    try {
      const res = await fetch(url);
      if(!res.ok){
        const txt = await res.text();
        throw new Error(`API error: ${res.status}\n${txt}`);
      }
      const json = await res.json();
      updateMeta(json.meta);
      drawScatter(json.rows);
    } catch(err){
      alert(err.message);
      console.error(err);
    }
  }

  async function fetchHist() {
    const vehicle_id = $('vehicle_id').value;
    const start_ts = $('start_ts').value;
    const end_ts = $('end_ts').value;
    const limit = $('limit').value || 200;
    const backend = $('backend').value || 'bigquery';
    const project = $('project').value || 't2-integration';

    const qs = new URLSearchParams({
      vehicle_id, start_ts, end_ts, limit, backend, project
    });
    const url = `/api/hist?${qs.toString()}`;
    try {
      const res = await fetch(url);
      if(!res.ok){
        const txt = await res.text();
        throw new Error(`API error: ${res.status}\n${txt}`);
      }
      const json = await res.json();
      drawHist(json.times, json.values);
    } catch(err){
      alert(err.message);
      console.error(err);
    }
  }

  function updateMeta(meta){
    if(!meta){ $('matched').textContent = '-'; return; }
    const s = `${meta.matched_q1 || 0}/${meta.total_rows || 0} (${meta.matched_pct || 0}%)`;
    $('matched').textContent = s;
  }

  function drawScatter(rows){
    // rows: list of objects with __time, acceleration, lateral_error
    if(!rows || rows.length===0){
      Plotly.purge('scatter');
      return;
    }
    const x = rows.map(r => parseISO(r.__time));
    const acc = rows.map(r => (r.acceleration==null ? NaN : +r.acceleration));
    const lat = rows.map(r => (r.lateral_error==null ? NaN : +r.lateral_error));

    const t1 = {
      x, y: acc, mode:'markers', name:'acceleration', marker:{color:'#1f77b4', size:6}
    };
    const t2 = {
      x, y: lat, mode:'markers', name:'lateral_error', marker:{color:'#ff7f0e', size:6}
    };

    const layout = {
      title:'Scatter (acceleration vs lateral_error over time)',
      xaxis:{title:'time', type:'date'},
      yaxis:{title:'value'},
      legend:{orientation:'v', x:0.92, y:0.95},
      margin:{l:60, r:130, t:40, b:60},
    };
    Plotly.newPlot('scatter',[t1,t2],layout, {responsive:true});
  }

  function drawHist(times, values){
    const v = (values || []).map(x => +x).filter(x => !isNaN(x));
    const trace = {x:v, type:'histogram', marker:{color:'#1f77b4'}};
    const layout = {
      title:'Histogram (linear_accel_y)',
      xaxis:{title:'linear_accel_y'},
      yaxis:{title:'count'},
      margin:{l:60, r:20, t:40, b:60},
    };
    Plotly.newPlot('hist',[trace],layout, {responsive:true});
  }

  // wire up
  document.addEventListener('DOMContentLoaded', ()=>{
    $('btnScatter').addEventListener('click', fetchScatter);
    $('btnHist').addEventListener('click', fetchHist);

    // quick auto-fetch
    // fetchScatter();
    // fetchHist();
  });
})();