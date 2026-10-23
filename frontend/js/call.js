// WebRTC peer connection + room signaling over WebSocket (two participants).
export class CallSession {
  constructor(room, iceServers, stream, h) {
    Object.assign(this, { room, iceServers, stream, h });
    this.pc = null; this.ws = null; this.pending = []; this.closing = false;
  }

  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = this.ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(this.room)}`);
    ws.onopen = () => this.h.onOpen();
    ws.onmessage = (e) => {
      let m; try { m = JSON.parse(e.data); } catch { return; }
      this.handle(m).catch((err) => this.h.onError(`WebRTC error: ${err.message}`));
    };
    ws.onerror = () => this.h.onError('Signaling connection error. Is the server running?');
    ws.onclose = (e) => { if (!this.closing) this.h.onClosed(e.code); };
  }

  send(o) { if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(o)); }
  sendProfile() { this.send({ type: 'profile', profile: this.h.getProfile() }); }

  newPc() {
    this.closePc();
    const pc = new RTCPeerConnection({ iceServers: this.iceServers });
    this.stream.getTracks().forEach((t) => pc.addTrack(t, this.stream));
    pc.onicecandidate = (e) => { if (e.candidate) this.send({ type: 'ice', candidate: e.candidate }); };
    pc.ontrack = (e) => this.h.onRemoteStream(e.streams[0]);
    pc.onconnectionstatechange = () => this.h.onConn(pc.connectionState);
    this.pc = pc;
    return pc;
  }

  closePc() { if (this.pc) { this.pc.close(); this.pc = null; } this.pending = []; }

  async flush() {
    for (const c of this.pending) { try { await this.pc.addIceCandidate(c); } catch { /* ignore */ } }
    this.pending = [];
  }

  async handle(m) {
    switch (m.type) {
      case 'joined': this.h.onJoined(m); if (m.peers > 0) this.sendProfile(); break;
      case 'peer_joined': {
        this.h.onPeerJoined(); this.sendProfile();
        const pc = this.newPc();
        await pc.setLocalDescription(await pc.createOffer());
        this.send({ type: 'offer', sdp: pc.localDescription });
        break;
      }
      case 'peer_left': this.closePc(); this.h.onPeerLeft(); break;
      case 'offer': {
        const pc = this.newPc();
        await pc.setRemoteDescription(m.sdp); await this.flush();
        await pc.setLocalDescription(await pc.createAnswer());
        this.send({ type: 'answer', sdp: pc.localDescription });
        break;
      }
      case 'answer': if (this.pc) { await this.pc.setRemoteDescription(m.sdp); await this.flush(); } break;
      case 'ice':
        if (this.pc && this.pc.remoteDescription) { try { await this.pc.addIceCandidate(m.candidate); } catch { /* ignore */ } }
        else this.pending.push(m.candidate);
        break;
      case 'profile': this.h.onPeerProfile(m.profile); break;
      case 'utterance': this.h.onUtterance(m); break;
      case 'room_full': case 'error': this.h.onError(m.message || 'Server error'); break;
    }
  }

  leave() {
    this.closing = true;
    if (this.ws) this.ws.close();
    this.closePc();
  }
}
