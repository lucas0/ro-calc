'use strict';

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const url = require('url');

// ─── Config ──────────────────────────────────────────────────────────────────

const PORT = process.env.PORT || 9500;
const ROOT_DIR   = __dirname;                              // refine calculator lives here
const STATIC_DIR = path.join(__dirname, 'mvp-tracker');   // MVP tracker lives here
const DATA_DIR   = path.join(STATIC_DIR, 'data');
const MVP_PREFIX = '/mvp';                                  // ro-calc.com/mvp

// Ensure data directory exists
if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  console.log(`Created data directory: ${DATA_DIR}`);
}

// Load config from file as fallback
let fileConfig = {};
try {
  const cfgPath = path.join(STATIC_DIR, 'config.json');
  if (fs.existsSync(cfgPath)) {
    fileConfig = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
  }
} catch (e) {
  // ignore
}

const DISCORD_CLIENT_ID     = process.env.DISCORD_CLIENT_ID     || fileConfig.discordClientId     || '';
const DISCORD_CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET || fileConfig.discordClientSecret || '';
const BASE_URL              = process.env.BASE_URL              || fileConfig.baseUrl              || `http://localhost:${PORT}`;
const DISCORD_REDIRECT_URI  = process.env.DISCORD_REDIRECT_URI  || fileConfig.discordRedirectUri  || `${BASE_URL}/api/auth/discord/callback`;

// ─── MIME types ───────────────────────────────────────────────────────────────

const MIME = {
  '.html': 'text/html',
  '.css': 'text/css',
  '.js': 'application/javascript',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.json': 'application/json',
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function genToken(n) {
  return crypto.randomBytes(n).toString('hex');
}

function hashPw(salt, pw) {
  return crypto.createHash('sha256').update(salt + pw).digest('hex');
}

function readJson(file, def) {
  try {
    const raw = fs.readFileSync(path.join(DATA_DIR, file), 'utf8');
    return JSON.parse(raw);
  } catch (e) {
    return def;
  }
}

function writeJson(file, data) {
  fs.writeFileSync(path.join(DATA_DIR, file), JSON.stringify(data, null, 2), 'utf8');
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      if (!body) return resolve({});
      try { resolve(JSON.parse(body)); }
      catch (e) { reject(new Error('Invalid JSON')); }
    });
    req.on('error', reject);
  });
}

function setCorsHeaders(res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,X-Session-Token');
}

function jsonRes(res, status, data) {
  setCorsHeaders(res);
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(data));
}

function getUser(req) {
  const token = req.headers['x-session-token'];
  if (!token) return null;
  const sessions = readJson('sessions.json', {});
  const session = sessions[token];
  if (!session) return null;
  const users = readJson('users.json', []);
  return users.find(u => u.id === session.userId) || null;
}

function inGroup(groups, groupId, userId) {
  const group = groups.find(g => g.id === groupId);
  if (!group) return null;
  if (!group.members.includes(userId)) return null;
  return group;
}

function createSession(userId) {
  const token = genToken(16);
  const sessions = readJson('sessions.json', {});
  sessions[token] = { userId, createdAt: new Date().toISOString() };
  writeJson('sessions.json', sessions);
  return token;
}

function httpsPost(hostname, reqPath, headers, body) {
  return new Promise((resolve, reject) => {
    const bodyStr = typeof body === 'string' ? body : JSON.stringify(body);
    const options = {
      hostname,
      path: reqPath,
      method: 'POST',
      headers: {
        'Content-Length': Buffer.byteLength(bodyStr),
        ...headers,
      },
    };
    const req = https.request(options, res => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
        catch (e) { resolve({ status: res.statusCode, body: data }); }
      });
    });
    req.on('error', reject);
    req.write(bodyStr);
    req.end();
  });
}

function httpsGet(hostname, reqPath, headers) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname,
      path: reqPath,
      method: 'GET',
      headers: headers || {},
    };
    const req = https.request(options, res => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
        catch (e) { resolve({ status: res.statusCode, body: data }); }
      });
    });
    req.on('error', reject);
    req.end();
  });
}

function userPublic(user) {
  return {
    id: user.id,
    characterName: user.characterName,
    discordId: user.discordId || null,
    discordUsername: user.discordUsername || null,
    discordAvatar: user.discordAvatar || null,
  };
}

// ─── Static file serving ──────────────────────────────────────────────────────

function serveFile(res, filePath, allowedRoot) {
  // Security: ensure path stays within allowed root
  const resolved = path.resolve(filePath);
  if (!resolved.startsWith(allowedRoot)) {
    res.writeHead(403, { 'Content-Type': 'text/plain' });
    res.end('Forbidden');
    return;
  }
  fs.readFile(resolved, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('Not Found');
      return;
    }
    const ext = path.extname(resolved).toLowerCase();
    const mime = MIME[ext] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': mime });
    res.end(data);
  });
}

function serveStatic(req, res, reqPath) {
  // MVP Tracker at /mvp (or /mvp/*)
  if (reqPath === MVP_PREFIX || reqPath === MVP_PREFIX + '/' || reqPath.startsWith(MVP_PREFIX + '/')) {
    let sub = reqPath.slice(MVP_PREFIX.length) || '/';
    if (sub === '/' || sub === '/index.html' || sub === '') {
      return serveFile(res, path.join(STATIC_DIR, 'index.html'), STATIC_DIR);
    }
    const relative = path.normalize(sub).replace(/^(\.\.[/\\])+/, '');
    return serveFile(res, path.join(STATIC_DIR, relative), STATIC_DIR);
  }

  // Refine calculator at root (/)
  if (reqPath === '/' || reqPath === '/index.html') {
    return serveFile(res, path.join(ROOT_DIR, 'index.html'), ROOT_DIR);
  }
  const relative = path.normalize(reqPath).replace(/^(\.\.[/\\])+/, '');
  return serveFile(res, path.join(ROOT_DIR, relative), ROOT_DIR);
}

// ─── Auth Handlers ────────────────────────────────────────────────────────────

async function handleAuth(req, res, segments, query) {
  // POST /api/auth/register
  if (segments[2] === 'register' && req.method === 'POST') {
    let body;
    try { body = await parseBody(req); } catch (e) { return jsonRes(res, 400, { error: 'Invalid JSON' }); }

    const { characterName, password } = body;
    if (!characterName || !password) return jsonRes(res, 400, { error: 'characterName and password required' });
    if (password.length < 6) return jsonRes(res, 400, { error: 'Password must be at least 6 characters' });

    const users = readJson('users.json', []);
    const exists = users.find(u => u.characterName.toLowerCase() === characterName.toLowerCase());
    if (exists) return jsonRes(res, 409, { error: 'Character name already taken' });

    const salt = genToken(8);
    const user = {
      id: genToken(16),
      characterName,
      salt,
      hash: hashPw(salt, password),
      createdAt: new Date().toISOString(),
      discordId: null,
      discordUsername: null,
      discordAvatar: null,
    };
    users.push(user);
    writeJson('users.json', users);

    const token = createSession(user.id);
    return jsonRes(res, 201, { token, user: userPublic(user) });
  }

  // POST /api/auth/login
  if (segments[2] === 'login' && req.method === 'POST') {
    let body;
    try { body = await parseBody(req); } catch (e) { return jsonRes(res, 400, { error: 'Invalid JSON' }); }

    const { characterName, password } = body;
    if (!characterName || !password) return jsonRes(res, 400, { error: 'characterName and password required' });

    const users = readJson('users.json', []);
    const user = users.find(u => u.characterName.toLowerCase() === characterName.toLowerCase());
    if (!user) return jsonRes(res, 401, { error: 'Invalid credentials' });
    if (!user.hash || hashPw(user.salt, password) !== user.hash) return jsonRes(res, 401, { error: 'Invalid credentials' });

    const token = createSession(user.id);
    return jsonRes(res, 200, { token, user: userPublic(user) });
  }

  // GET /api/auth/me
  if (segments[2] === 'me' && req.method === 'GET') {
    const user = getUser(req);
    if (!user) return jsonRes(res, 401, { error: 'Unauthorized' });
    return jsonRes(res, 200, userPublic(user));
  }

  // POST /api/auth/logout
  if (segments[2] === 'logout' && req.method === 'POST') {
    const user = getUser(req);
    if (!user) return jsonRes(res, 401, { error: 'Unauthorized' });
    const token = req.headers['x-session-token'];
    const sessions = readJson('sessions.json', {});
    delete sessions[token];
    writeJson('sessions.json', sessions);
    return jsonRes(res, 200, { ok: true });
  }

  // Discord routes
  if (segments[2] === 'discord') {
    return handleDiscordAuth(req, res, segments, query);
  }

  return jsonRes(res, 404, { error: 'Not found' });
}

async function handleDiscordAuth(req, res, segments, query) {
  const sub = segments[3];

  // GET /api/auth/discord/init
  if (sub === 'init' && req.method === 'GET') {
    if (!DISCORD_CLIENT_ID) return jsonRes(res, 503, { error: 'Discord not configured', configured: false });

    const state = genToken(16);
    const sessionToken = query.session || null;
    const states = readJson('oauth_states.json', {});
    states[state] = { createdAt: new Date().toISOString(), sessionToken };
    writeJson('oauth_states.json', states);

    const redirectUri = encodeURIComponent(DISCORD_REDIRECT_URI);
    const discordUrl = `https://discord.com/oauth2/authorize?client_id=${DISCORD_CLIENT_ID}&redirect_uri=${redirectUri}&response_type=code&scope=identify&state=${state}`;
    return jsonRes(res, 200, { url: discordUrl });
  }

  // GET /api/auth/discord/callback
  if (sub === 'callback' && req.method === 'GET') {
    const { code, state, error: oauthError } = query;

    const closeWithError = (msg) => {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(`<script>window.opener && window.opener.postMessage({type:'discord_error',error:${JSON.stringify(msg)}}, '*'); window.close();</script>`);
    };

    if (oauthError) return closeWithError(oauthError);
    if (!state) return closeWithError('Missing state');

    const states = readJson('oauth_states.json', {});
    const stateData = states[state];
    if (!stateData) return closeWithError('Invalid or expired state');

    // Delete used state
    delete states[state];
    writeJson('oauth_states.json', states);

    if (!code) return closeWithError('Missing code');

    try {
      const redirectUri = DISCORD_REDIRECT_URI;
      const params = new url.URLSearchParams({
        client_id: DISCORD_CLIENT_ID,
        client_secret: DISCORD_CLIENT_SECRET,
        grant_type: 'authorization_code',
        code,
        redirect_uri: redirectUri,
      });

      const tokenRes = await httpsPost(
        'discord.com',
        '/api/v10/oauth2/token',
        { 'Content-Type': 'application/x-www-form-urlencoded' },
        params.toString()
      );

      if (tokenRes.status !== 200 || !tokenRes.body.access_token) {
        return closeWithError('Failed to exchange code for token');
      }

      const accessToken = tokenRes.body.access_token;
      const userRes = await httpsGet(
        'discord.com',
        '/api/v10/users/@me',
        { Authorization: `Bearer ${accessToken}` }
      );

      if (userRes.status !== 200 || !userRes.body.id) {
        return closeWithError('Failed to fetch Discord user');
      }

      const discordUser = userRes.body;
      const users = readJson('users.json', []);

      let appUser;
      if (stateData.sessionToken) {
        // Link discord to existing account
        const sessions = readJson('sessions.json', {});
        const session = sessions[stateData.sessionToken];
        if (session) {
          appUser = users.find(u => u.id === session.userId);
          if (appUser) {
            appUser.discordId = discordUser.id;
            appUser.discordUsername = discordUser.username;
            appUser.discordAvatar = discordUser.avatar || null;
            writeJson('users.json', users);
          }
        }
      }

      if (!appUser) {
        // Find by discordId or create new user
        appUser = users.find(u => u.discordId === discordUser.id);
        if (!appUser) {
          appUser = {
            id: genToken(16),
            characterName: discordUser.username,
            salt: null,
            hash: null,
            createdAt: new Date().toISOString(),
            discordId: discordUser.id,
            discordUsername: discordUser.username,
            discordAvatar: discordUser.avatar || null,
          };
          users.push(appUser);
        } else {
          appUser.discordUsername = discordUser.username;
          appUser.discordAvatar = discordUser.avatar || null;
        }
        writeJson('users.json', users);
      }

      const token = createSession(appUser.id);
      const userData = userPublic(appUser);

      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(`<script>window.opener && window.opener.postMessage({type:'discord_success',token:${JSON.stringify(token)},user:${JSON.stringify(userData)}}, '*'); window.close();</script>`);
    } catch (err) {
      console.error('Discord callback error:', err);
      const closeErr = (msg) => {
        res.writeHead(200, { 'Content-Type': 'text/html' });
        res.end(`<script>window.opener && window.opener.postMessage({type:'discord_error',error:${JSON.stringify(msg)}}, '*'); window.close();</script>`);
      };
      closeErr('Internal server error');
    }
    return;
  }

  // POST /api/auth/discord/link
  if (sub === 'link' && req.method === 'POST') {
    if (!DISCORD_CLIENT_ID) return jsonRes(res, 503, { error: 'Discord not configured', configured: false });

    const user = getUser(req);
    if (!user) return jsonRes(res, 401, { error: 'Unauthorized' });

    const sessionToken = req.headers['x-session-token'];
    const state = genToken(16);
    const states = readJson('oauth_states.json', {});
    states[state] = { createdAt: new Date().toISOString(), sessionToken };
    writeJson('oauth_states.json', states);

    const redirectUri = encodeURIComponent(DISCORD_REDIRECT_URI);
    const discordUrl = `https://discord.com/oauth2/authorize?client_id=${DISCORD_CLIENT_ID}&redirect_uri=${redirectUri}&response_type=code&scope=identify&state=${state}`;
    return jsonRes(res, 200, { url: discordUrl });
  }

  return jsonRes(res, 404, { error: 'Not found' });
}

// ─── Groups Handler ───────────────────────────────────────────────────────────

async function handleGroups(req, res, segments, query) {
  const user = getUser(req);
  if (!user) return jsonRes(res, 401, { error: 'Unauthorized' });

  // GET /api/groups
  if (!segments[2] && req.method === 'GET') {
    const allGroups = readJson('groups.json', []);
    const userGroups = allGroups.filter(g => g.members.includes(user.id));
    return jsonRes(res, 200, userGroups);
  }

  // POST /api/groups
  if (!segments[2] && req.method === 'POST') {
    let body;
    try { body = await parseBody(req); } catch (e) { return jsonRes(res, 400, { error: 'Invalid JSON' }); }

    const { name } = body;
    if (!name) return jsonRes(res, 400, { error: 'name required' });

    const groups = readJson('groups.json', []);
    const group = {
      id: genToken(8),
      name,
      members: [user.id],
      inviteCode: genToken(4).toUpperCase(),
      createdBy: user.id,
      createdAt: new Date().toISOString(),
      discordWebhook: null,
    };
    groups.push(group);
    writeJson('groups.json', groups);
    return jsonRes(res, 201, group);
  }

  // POST /api/groups/join
  if (segments[2] === 'join' && req.method === 'POST') {
    let body;
    try { body = await parseBody(req); } catch (e) { return jsonRes(res, 400, { error: 'Invalid JSON' }); }

    const { inviteCode } = body;
    if (!inviteCode) return jsonRes(res, 400, { error: 'inviteCode required' });

    const groups = readJson('groups.json', []);
    const group = groups.find(g => g.inviteCode.toLowerCase() === inviteCode.toLowerCase());
    if (!group) return jsonRes(res, 404, { error: 'Group not found' });

    if (!group.members.includes(user.id)) {
      group.members.push(user.id);
      writeJson('groups.json', groups);
    }
    return jsonRes(res, 200, group);
  }

  const groupId = segments[2];
  const groups = readJson('groups.json', []);

  // GET /api/groups/:id
  if (!segments[3] && req.method === 'GET') {
    const group = inGroup(groups, groupId, user.id);
    if (!group) return jsonRes(res, 404, { error: 'Group not found or not a member' });
    return jsonRes(res, 200, group);
  }

  // PUT /api/groups/:id/webhook
  if (segments[3] === 'webhook' && req.method === 'PUT') {
    const group = inGroup(groups, groupId, user.id);
    if (!group) return jsonRes(res, 404, { error: 'Group not found or not a member' });

    let body;
    try { body = await parseBody(req); } catch (e) { return jsonRes(res, 400, { error: 'Invalid JSON' }); }

    group.discordWebhook = body.webhookUrl || null;
    writeJson('groups.json', groups);
    return jsonRes(res, 200, { ok: true });
  }

  // GET /api/groups/:id/members
  if (segments[3] === 'members' && req.method === 'GET') {
    const group = inGroup(groups, groupId, user.id);
    if (!group) return jsonRes(res, 404, { error: 'Group not found or not a member' });

    const allUsers = readJson('users.json', []);
    const members = group.members
      .map(mid => allUsers.find(u => u.id === mid))
      .filter(Boolean)
      .map(u => ({
        id: u.id,
        characterName: u.characterName,
        discordUsername: u.discordUsername || null,
        discordAvatar: u.discordAvatar || null,
      }));
    return jsonRes(res, 200, members);
  }

  return jsonRes(res, 404, { error: 'Not found' });
}

// ─── Timers Handler ───────────────────────────────────────────────────────────

async function handleTimers(req, res, segments, query) {
  const user = getUser(req);
  if (!user) return jsonRes(res, 401, { error: 'Unauthorized' });

  // GET /api/timers?groupId=
  if (!segments[2] && req.method === 'GET') {
    const groupId = query.groupId;
    if (!groupId) return jsonRes(res, 400, { error: 'groupId required' });
    const groups = readJson('groups.json', []);
    if (!inGroup(groups, groupId, user.id)) return jsonRes(res, 403, { error: 'Not a member' });
    const timers = readJson(`timers_${groupId}.json`, {});
    return jsonRes(res, 200, timers);
  }

  // POST /api/timers
  if (!segments[2] && req.method === 'POST') {
    let body;
    try { body = await parseBody(req); } catch (e) { return jsonRes(res, 400, { error: 'Invalid JSON' }); }

    const { groupId, timerKey, mvpId, map, deathTime, killedByOthers, specificMvpId, party } = body;
    if (!groupId || !timerKey) return jsonRes(res, 400, { error: 'groupId and timerKey required' });

    const groups = readJson('groups.json', []);
    if (!inGroup(groups, groupId, user.id)) return jsonRes(res, 403, { error: 'Not a member' });

    const timers = readJson(`timers_${groupId}.json`, {});
    const timer = {
      groupId,
      timerKey,
      mvpId,
      map,
      deathTime,
      killedByOthers,
      specificMvpId,
      party,
      registeredBy: user.id,
      updatedAt: Date.now(),
    };
    timers[timerKey] = timer;
    writeJson(`timers_${groupId}.json`, timers);
    return jsonRes(res, 200, timer);
  }

  // DELETE /api/timers/:key?groupId=
  if (segments[2] && req.method === 'DELETE') {
    const timerKey = decodeURIComponent(segments[2]);
    const groupId = query.groupId;
    if (!groupId) return jsonRes(res, 400, { error: 'groupId required' });

    const groups = readJson('groups.json', []);
    if (!inGroup(groups, groupId, user.id)) return jsonRes(res, 403, { error: 'Not a member' });

    const timers = readJson(`timers_${groupId}.json`, {});
    delete timers[timerKey];
    writeJson(`timers_${groupId}.json`, timers);
    return jsonRes(res, 200, { ok: true });
  }

  return jsonRes(res, 404, { error: 'Not found' });
}

// ─── Kills Handler ────────────────────────────────────────────────────────────

async function handleKills(req, res, segments, query) {
  const user = getUser(req);
  if (!user) return jsonRes(res, 401, { error: 'Unauthorized' });

  // GET /api/kills?groupId=
  if (!segments[2] && req.method === 'GET') {
    const groupId = query.groupId;
    if (!groupId) return jsonRes(res, 400, { error: 'groupId required' });
    const groups = readJson('groups.json', []);
    if (!inGroup(groups, groupId, user.id)) return jsonRes(res, 403, { error: 'Not a member' });
    const kills = readJson(`kills_${groupId}.json`, []);
    return jsonRes(res, 200, kills);
  }

  // POST /api/kills
  if (!segments[2] && req.method === 'POST') {
    let body;
    try { body = await parseBody(req); } catch (e) { return jsonRes(res, 400, { error: 'Invalid JSON' }); }

    const { groupId, mvpId, map, deathTime, specificMvpId, party, drops, killedByOthers, coords, unsoldItems } = body;
    if (!groupId) return jsonRes(res, 400, { error: 'groupId required' });

    const groups = readJson('groups.json', []);
    if (!inGroup(groups, groupId, user.id)) return jsonRes(res, 403, { error: 'Not a member' });

    const kills = readJson(`kills_${groupId}.json`, []);
    const kill = {
      id: genToken(8),
      groupId,
      mvpId,
      map,
      deathTime,
      specificMvpId,
      party,
      drops,
      killedByOthers,
      coords,
      unsoldItems,
      registeredBy: user.id,
      createdAt: new Date().toISOString(),
    };
    kills.unshift(kill);
    if (kills.length > 500) kills.length = 500;
    writeJson(`kills_${groupId}.json`, kills);
    return jsonRes(res, 201, kill);
  }

  return jsonRes(res, 404, { error: 'Not found' });
}

// ─── Prefs Handler ────────────────────────────────────────────────────────────

async function handlePrefs(req, res, segments, query) {
  const user = getUser(req);
  if (!user) return jsonRes(res, 401, { error: 'Unauthorized' });

  // GET /api/prefs
  if (req.method === 'GET') {
    const prefs = readJson('user_prefs.json', {});
    return jsonRes(res, 200, prefs[user.id] || { trackedMVPs: [], mvpOrder: [] });
  }

  // POST /api/prefs
  if (req.method === 'POST') {
    let body;
    try { body = await parseBody(req); } catch (e) { return jsonRes(res, 400, { error: 'Invalid JSON' }); }

    const { trackedMVPs, mvpOrder } = body;
    const prefs = readJson('user_prefs.json', {});
    prefs[user.id] = { trackedMVPs: trackedMVPs || [], mvpOrder: mvpOrder || [] };
    writeJson('user_prefs.json', prefs);
    return jsonRes(res, 200, { ok: true });
  }

  return jsonRes(res, 405, { error: 'Method not allowed' });
}

// ─── Notify Handler ───────────────────────────────────────────────────────────

async function handleNotify(req, res, segments, query) {
  const user = getUser(req);
  if (!user) return jsonRes(res, 401, { error: 'Unauthorized' });

  // POST /api/notify/discord?groupId=
  if (segments[2] === 'discord' && req.method === 'POST') {
    const groupId = query.groupId;
    if (!groupId) return jsonRes(res, 400, { error: 'groupId required' });

    const groups = readJson('groups.json', []);
    const group = inGroup(groups, groupId, user.id);
    if (!group) return jsonRes(res, 403, { error: 'Not a member' });

    if (!group.discordWebhook) return jsonRes(res, 400, { error: 'No webhook configured for this group' });

    let body;
    try { body = await parseBody(req); } catch (e) { return jsonRes(res, 400, { error: 'Invalid JSON' }); }

    try {
      const webhookUrl = new url.URL(group.discordWebhook);
      const result = await httpsPost(
        webhookUrl.hostname,
        webhookUrl.pathname + webhookUrl.search,
        { 'Content-Type': 'application/json' },
        JSON.stringify({ content: body.content, embeds: body.embeds })
      );
      return jsonRes(res, 200, { ok: true, status: result.status, body: result.body });
    } catch (err) {
      console.error('Discord webhook error:', err);
      return jsonRes(res, 500, { error: 'Failed to send webhook' });
    }
  }

  return jsonRes(res, 404, { error: 'Not found' });
}

// ─── Main router ──────────────────────────────────────────────────────────────

async function handleRequest(req, res) {
  const parsed = url.parse(req.url, true);
  const pathname = parsed.pathname;
  const query = parsed.query;

  // Handle OPTIONS preflight for all routes
  if (req.method === 'OPTIONS') {
    setCorsHeaders(res);
    res.writeHead(200);
    res.end();
    return;
  }

  // API routes
  if (pathname.startsWith('/api/')) {
    // segments: ['api', 'auth'|'groups'|..., ...]
    const segments = pathname.split('/').filter(Boolean);

    try {
      if (segments[1] === 'auth') return await handleAuth(req, res, segments, query);
      if (segments[1] === 'groups') return await handleGroups(req, res, segments, query);
      if (segments[1] === 'timers') return await handleTimers(req, res, segments, query);
      if (segments[1] === 'kills') return await handleKills(req, res, segments, query);
      if (segments[1] === 'prefs') return await handlePrefs(req, res, segments, query);
      if (segments[1] === 'notify') return await handleNotify(req, res, segments, query);
      return jsonRes(res, 404, { error: 'API route not found' });
    } catch (err) {
      console.error('API error:', err);
      return jsonRes(res, 500, { error: 'Internal server error' });
    }
  }

  // Static file serving
  serveStatic(req, res, pathname);
}

// ─── Start server ─────────────────────────────────────────────────────────────

const server = http.createServer(handleRequest);

server.listen(PORT, () => {
  console.log(`MVP Tracker server running on port ${PORT}`);
  console.log(`Static dir: ${STATIC_DIR}`);
  console.log(`Data dir:   ${DATA_DIR}`);
  console.log(`Base URL:   ${BASE_URL}`);
  console.log(`Discord:    ${DISCORD_CLIENT_ID ? 'configured' : 'not configured'}`);
});

server.on('error', (err) => {
  console.error('Server error:', err);
  process.exit(1);
});
