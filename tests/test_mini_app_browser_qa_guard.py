"""Exercise the actual browser-gate watchdog with owned fake processes only."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


class BrowserGateWatchdogTests(unittest.TestCase):
    def test_timeout_never_becomes_pass_when_child_exits_zero(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for the approved browser QA gate")
        root = Path(__file__).resolve().parent.parent
        code = r"""
const fs=require('fs'),vm=require('vm'),{EventEmitter}=require('events');
const source=fs.readFileSync(process.argv[1],'utf8');
const expression=source.match(/const result=await (new Promise\(resolve=>\{[^\n]+\}\));\r?\n    fs.writeFileSync\(path.join\(dir,'runner.log'\)/);
if(!expression)throw new Error('Actual watchdog expression must be found');
(async()=>{
  const results=[];
  for(const scenario of ['normal','timeout-race','timeout-kill-error','spawn-error']){
    let owned,timer,killArgs,childKilled=false,cleared=false;
    const spawn=(command,args)=>{
      const child=new EventEmitter();child.pid=12345;
      child.stdout=new EventEmitter();child.stderr=new EventEmitter();
      child.kill=()=>{childKilled=true;};
      if(command==='taskkill'){
        killArgs=args;
        queueMicrotask(()=>{owned.emit('exit',0);child.emit(scenario==='timeout-kill-error'?'error':'exit',scenario==='timeout-kill-error'?new Error('owned fake stop failed'):0);});
      }else owned=child;
      return child;
    };
    const result=vm.runInNewContext(expression[1],{spawn,process:{execPath:'OWN_FAKE_NODE',env:{}},args:[],root:'OWN_FAKE_ROOT',dir:'OWN_FAKE_OUTPUT',setTimeout:fn=>{timer=fn;return 7;},clearTimeout:id=>{if(id!==7)throw new Error('Wrong timer');cleared=true;}});
    if(scenario==='normal'){owned.stdout.emit('data','x'.repeat(8000));owned.emit('exit',0);}
    else if(scenario==='spawn-error')owned.emit('error',new Error('owned fake spawn failed'));
    else timer();
    results.push({scenario,...await result,killArgs,childKilled,cleared});
    await new Promise(r=>setImmediate(r));
  }
  process.stdout.write(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run(
            [node, "-e", code, str(root / "scripts/mini_app_redesign_matrix_qa.cjs")],
            text=True, capture_output=True, timeout=10, check=True,
        )
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 4)
        for row in rows:
            with self.subTest(scenario=row["scenario"]):
                self.assertTrue(row["cleared"])
                if row["scenario"] == "normal":
                    self.assertEqual(row["code"], 0)
                    self.assertEqual(len(row["log"]), 5000)
                    self.assertNotIn("killArgs", row)
                else:
                    self.assertNotEqual(row["code"], 0)
                    if row["scenario"].startswith("timeout"):
                        self.assertEqual(row["killArgs"], ["/PID", "12345", "/T", "/F"])
                        self.assertIn("exceeded 180s", row["log"])
