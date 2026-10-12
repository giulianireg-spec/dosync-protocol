// An independent WoT consumer uses a DoSync hub's devices, knowing nothing of DoSync.
//
// Eclipse Thingweb node-wot discovers the hub (/.well-known/wot), reads one
// device's Thing Description, invokes its actions and reads a property -- with
// nothing but what the TD says. Prints one JSON object of what happened, for
// tests/test_wot_interop.py to judge. Usage:
//   node consume.mjs <hub-url> <token|""> <device-id> <action>... --read <property>
import core from "@node-wot/core";
import http from "@node-wot/binding-http";

const { Servient } = core;
const { HttpClientFactory } = http;

const [hub, token, deviceId, ...rest] = process.argv.slice(2);
const readIdx = rest.indexOf("--read");
const actions = readIdx >= 0 ? rest.slice(0, readIdx) : rest;
const property = readIdx >= 0 ? rest[readIdx + 1] : null;
const out = { directory: null, td_title: null, actions: {}, property: null };

const servient = new Servient();
servient.addClientFactory(new HttpClientFactory(null));
const WoT = await servient.start();
try {
  const dir = await WoT.requestThingDescription(`${hub}/.well-known/wot`);
  out.directory = dir["@type"] ?? null;
  if (token) servient.addCredentials({ [`urn:dosync:device:${deviceId}`]: { token } });
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  const res = await fetch(`${hub}/v1/things/${deviceId}`, { headers });
  const td = await res.json();
  out.td_title = td.title;
  out.td_actions = Object.keys(td.actions ?? {});
  const thing = await WoT.consume(td);
  for (const name of actions) {
    try {
      const output = await thing.invokeAction(name);
      out.actions[name] = { ok: true, value: await output.value() };
    } catch (e) {
      out.actions[name] = { ok: false, error: String(e.message ?? e).slice(0, 300) };
    }
  }
  if (property) {
    try {
      const value = await (await thing.readProperty(property)).value();
      out.property = { ok: true, value };
    } catch (e) {
      out.property = { ok: false, error: String(e.message ?? e).slice(0, 300) };
    }
  }
} finally {
  await servient.shutdown();
}
console.log(JSON.stringify(out));
