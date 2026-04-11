Yes. It is feasible, but only if you treat RKE2 as an operational design template, not as the literal substrate.

RKE2’s reusable ideas are the ones around packaging and lifecycle: a single binary, installed as a long-running system service; a clear server vs agent split; config-file-first operation; secure node join tokens; a fixed registration address for joining nodes; and HA built around an odd number of control-plane servers. Its daemon supervises child processes and runs indefinitely until terminated. Those are the parts worth copying.

Your current Mission Control repo already has the beginnings of this model. The project describes mc as a Rust-native compiled gateway, says Mission Control can bootstrap resident mc node daemons using join tokens and install bundles, persists node config locally, and already exposes lease/heartbeat-style operations such as mc ops mission --action start|heartbeat|commit|release. The current mc integration is also already doing SSE/matrix feed work and MQTT-based inbox updates.

So the honest answer is: you are not starting from zero. You are closer to an RKE2-style agent fabric than it may feel.

The part I would not do is fork RKE2 and try to hollow it out. RKE2’s internals are heavily optimized around Kubernetes bootstrapping: extracting runtime assets, starting containerd, supervising kubelet, writing static pod manifests, launching etcd, and then letting the kubelet start the control-plane stack. That is excellent for Kubernetes, but it is the wrong center of gravity for an agent mesh. You want the RKE2 shape, not the Kubernetes payload.

The right product shape is:

mc-mesh server
mc-mesh agent
mc-mesh token
mc-mesh snapshot
mc-mesh upgrade
mc-mesh node

That gives you the RKE2 mental model without importing RKE2’s control-plane complexity.

My recommendation is to make mc-mesh the fleet/runtime substrate and keep Mission Control as the organizational brain. In other words:

Mission Control = missions, policies, approvals, artifact ledger, organizational state.
mc-mesh = node membership, desired state, work distribution, service-agent runtime, local execution, heartbeats, recovery, upgrades.

That separation matches your repo’s current direction, where Mission Control is already the orchestration/control plane and mc is the local/runtime gateway.

For the control plane, I would borrow RKE2’s HA pattern almost verbatim: one stable registration endpoint in front of an odd number of servers, usually 3. RKE2 explicitly recommends a fixed registration address plus three server nodes so the cluster can maintain quorum. That exact idea maps well to mc-mesh: one VIP/L4 load balancer, three mc-mesh server nodes, and all agents registering against that stable address.

For node bootstrap, copy RKE2’s secure-token approach. RKE2 documents secure tokens that include the cluster CA hash so a joining node can validate the server identity before sending credentials, and it supports expiring bootstrap tokens as well. For mc-mesh, do the same thing: short-lived join tokens plus mTLS enrollment on first join, then rotate into a long-lived node identity cert.

For the database, I would not replace Postgres as your main durable brain.

RKE2 uses embedded etcd because it needs a consensus store for Kubernetes cluster state, and its docs say embedded etcd is the only embedded datastore option that supports HA; embedded SQLite is explicitly not HA. That does not mean etcd is the right primary database for agent orchestration. Your domain wants rich querying, policies, history, audit trails, search, leases, artifacts, and human-facing operational views. Postgres is much better aligned to that.

So the best forward-looking architecture is:

Postgres HA for durable system-of-record state
NATS JetStream for eventing, work distribution, fanout, and edge/offline-friendly messaging
Local embedded store on each node for offline spool and last-known desired state

NATS JetStream is built into nats-server, supports replication for HA, supports work-queue retention, and its leaf-node model is explicitly meant to support local networks that can keep working when the hub/cloud connection is down. That last point is unusually well aligned with your “remote nodes may not always be connected” requirement.

If your control plane runs on Kubernetes, CloudNativePG is a strong fit for the Postgres side. Its current docs say it is Kubernetes-native, declarative, self-healing, and does not require an external failover management tool.

So my recommendation is:

v1 simplest path: Postgres only, with TTL leases and SKIP LOCKED-style work claiming
v1.5 / real mesh path: Postgres + NATS JetStream
avoid: etcd as your only primary application database

For heartbeat and stopping remote action, do not model this as a passive heartbeat check. Model it as a lease system.

Each node should hold:

a node session lease
one or more work leases
optionally a service-agent runtime lease

The agent renews those leases every few seconds. If lease renewal fails beyond grace, the node transitions locally into a policy-defined state:

strict: stop all mutable work immediately
safe-readonly: allow reads/monitoring, stop writes and external actions
autonomous: continue only whitelisted service agents with local policy + max autonomy TTL

That solves your remote/offline problem much better than a binary “connected/disconnected” flag, because you can explicitly define what an edge node is allowed to keep doing when it loses contact.

The crucial part is that the local watchdog must enforce lease expiry even if the control plane is unreachable. Otherwise a disconnected node can keep acting indefinitely, which is exactly what you said you do not want.

The minimum agent runtime I would build is:

persistent daemon under systemd
reverse control stream to the server
local runner supervisor
local spool/journal
capability inventory
lease renewer
kill/freeze hooks when leases expire
resumable state sync when connectivity returns

That gives you the RKE2 daemon feel, but for agents.

The best RKE2 features to borrow directly into mc-mesh are these:

Single binary + service install
server / agent split
Config file at a fixed OS path
Stable registration endpoint
Secure bootstrap token flow
Odd-number control plane for quorum
Snapshot / restore as a first-class command
Deterministic on-disk layout
Rolling upgrade channel model
Air-gap/offline install bundles

Those are the pieces that make RKE2 operationally good.

A good mc-mesh command surface would be:

mc-mesh server
mc-mesh agent

mc-mesh token create
mc-mesh token list
mc-mesh token revoke

mc-mesh node join
mc-mesh node status
mc-mesh node cordon
mc-mesh node drain
mc-mesh node uncordon

mc-mesh service-agent deploy
mc-mesh service-agent status
mc-mesh service-agent restart

mc-mesh snapshot save
mc-mesh snapshot restore

mc-mesh upgrade plan
mc-mesh upgrade apply

What it would take to build, realistically:

Prototype: feasible in 6–8 weeks if you stay disciplined and do not chase every feature.
That prototype would include:

1 binary
server/agent modes
secure join
node inventory
lease/heartbeat system
local watchdog
simple job/service-agent scheduling
Postgres-backed state
basic notifications

Production-grade v1: more like 4–6 months with a small focused team.
The hard parts are not the daemon itself. The hard parts are:

cert bootstrapping and rotation
idempotent upgrades
rollback safety
lease correctness under partitions
durable local replay
observability
policy enforcement
draining / maintenance workflows
operator experience

That is where “world-class” lives.

My strongest opinion here: do not build “agent Kubernetes.” Build “RKE2 for managed service-agents.” Keep the model narrow:

long-running resident daemons
known node inventory
service-agent placement
lease-based safety
offline-tolerant edge behavior
human-governed control plane

That is much more likely to become excellent.

The shortest strategic version is:

Keep Mission Control as the durable organizational control plane.
Build mc-mesh as a Rust single-binary fleet runtime.
Use 3 control-plane servers behind one registration endpoint.
Keep Postgres as the system of record.
Add NATS JetStream for the actual mesh/event/work layer.
Enforce lease expiry locally so disconnected nodes stop acting.
Copy RKE2’s ops model, not its Kubernetes internals.