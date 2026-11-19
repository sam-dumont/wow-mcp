-- Runs Polestar's own gear files (ItemDB.lua with the Polestar_ItemDB data
-- addon, Weights.lua, SpecData.lua) and prints what they hold as one JSON
-- object, for tools/gear_reference/export.mjs:
--
--   lua dump.lua <addon dir>
--
-- {items = {[id] = record}, sources = {[id] = {source...}}, quests = {[id] = quest},
--  meta = {...}, leveling = {...}, gear = {CLASSES, WEIGHTS, PROFICIENCY, ...}}
--
-- Items are parsed by ItemDB.lua's own parser (ItemDB.Get), sources and
-- quests by its ItemDB.Sources and ItemDB.Quest, so the export reads the data
-- exactly as the addon does. The files are data from a download: each runs in
-- an empty environment (no os, io, load, require), as text only, with stub
-- registries. Works on Lua 5.1 to 5.4.

local addon = assert(arg[1], "usage: lua dump.lua <Polestar addon dir>")

local deep
deep = setmetatable({}, { __index = function() return deep end, __call = function() return nil end })
local function noop() end

-- The addon's namespace, as far as the gear files reach into it.
local ns = {
	Fire = noop, On = noop,
	-- No localized names: the leveling data's English names stand.
	Names = { Item = noop, Area = noop, Quest = noop, Npc = noop },
}

local function base_env()
	local e = {
		pairs = pairs, ipairs = ipairs, next = next, type = type, tostring = tostring, tonumber = tonumber,
		select = select, unpack = unpack or table.unpack, pcall = pcall, setmetatable = setmetatable,
		string = { format = string.format, sub = string.sub, rep = string.rep, match = string.match,
			gmatch = string.gmatch, gsub = string.gsub, lower = string.lower, find = string.find },
		table = { insert = table.insert, concat = table.concat, sort = table.sort },
		math = { floor = math.floor, max = math.max, min = math.min, abs = math.abs, huge = math.huge },
		Polestar = ns,
	}
	e._G = e
	return setmetatable(e, { __index = function() return deep end })
end

local function run(path)
	local f = assert(io.open(path, "rb"), path .. " missing")
	local src = f:read("*a")
	f:close()
	if src:sub(1, 1) == "\27" then error(path .. ": precompiled chunk refused") end
	local e = base_env()
	local chunk, err
	if setfenv then
		chunk, err = loadstring(src, "@" .. path)
		if chunk then setfenv(chunk, e) end
	else
		chunk, err = load(src, "@" .. path, "t", e)
	end
	if not chunk then error(err) end
	chunk("Polestar", ns)
end

local gear = addon .. "/Polestar/Gear/"
ns.Gear = {}
run(gear .. "ItemDB.lua")
local DB = assert(ns.ItemDB, "ItemDB.lua registered no ItemDB")

-- Keep the ids and the meta the data addon registers (ItemDB keeps them local).
local ids, metas, levMeta, lev = {}, {}, nil, nil
local register, registerLeveling = DB.Register, DB.RegisterLeveling
DB.Register = function(m, items)
	if type(m) == "table" then metas[#metas + 1] = m end
	for id in pairs(items) do ids[id] = true end
	return register(m, items)
end
DB.RegisterLeveling = function(m, data)
	levMeta, lev = m, data
	return registerLeveling(m, data)
end
-- The order of Polestar_ItemDB.toc: Forever's stats overlay the Era table.
run(addon .. "/Polestar_ItemDB/Items.lua")
run(addon .. "/Polestar_ItemDB/Forever.lua")
DB.Load = function() return true end

local out = { items = {}, sources = {}, quests = {}, meta = metas, leveling = levMeta, names = {}, npcs = {}, zones = {}, professions = {} }
for id in pairs(ids) do
	local r = DB.Get(id)
	if r then
		out.items[tostring(id)] = { inv = r.inv, ilvl = r.ilvl, req = r.req, quality = r.quality, class = r.class,
			subclass = r.subclass, allow = r.allow, unique = r.unique, speed = r.speed, stats = r.stats }
	end
end
for id in pairs(lev and lev.src or {}) do
	out.sources[tostring(id)] = DB.Sources(id)
end
for id in pairs(lev and lev.quests or {}) do
	local q = DB.Quest(id)
	if q then
		out.quests[tostring(id)] = { name = q.name, level = q.level, req = q.req, side = q.side, zone = q.zone,
			race = q.reqRace, class = q.reqClass, dungeon = q.dungeon, choices = q.choices, items = q.items }
	end
end
for id, name in pairs(lev and lev.names or {}) do out.names[tostring(id)] = name end
for id, name in pairs(lev and lev.npcs or {}) do out.npcs[tostring(id)] = name end
for id, name in pairs(lev and lev.zones or {}) do out.zones[tostring(id)] = name end
out.professions = lev and lev.professions or {}

run(gear .. "Weights.lua")
run(gear .. "SpecData.lua")
local G = ns.Gear
local perCrit, armorShare = {}, {}
for class in pairs(G.CLASSES) do
	local by = {}
	for level = 1, 60 do by[level] = G.IntPerSpellCrit(class, level) end
	if by[60] then perCrit[class] = by end
end
for level = 1, 60 do armorShare[level] = G.ArmorShare(level) end
out.gear = {
	STAT_KEYS = G.STAT_KEYS, CLASSES = G.CLASSES, WEIGHTS = G.WEIGHTS, PROFICIENCY = G.PROFICIENCY,
	WEIGHT_BANDS = G.WEIGHT_BANDS, RACIALS = G.RACIALS,
	HEALTH_PER_STA = G.HEALTH_PER_STA, MANA_PER_INT = G.MANA_PER_INT,
	INT_PER_SPELL_CRIT = perCrit, ARMOR_SHARE = armorShare,
}

-- JSON out. Integer-keyed tables with holes (sets of subclasses) become
-- objects with string keys; keys are sorted so the output never depends on
-- the order of pairs().
local esc = { ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
local buf = {}
local function put(s) buf[#buf + 1] = s end
local function enc(v)
	local t = type(v)
	if t == "string" then
		put('"' .. v:gsub('[%c"\\]', function(c) return esc[c] or string.format("\\u%04x", c:byte()) end) .. '"')
	elseif t == "number" then
		if v ~= v or v == math.huge or v == -math.huge then put("null")
		elseif math.floor(v) == v and math.abs(v) < 2^53 then put(string.format("%d", v))
		else put(string.format("%.14g", v)) end
	elseif t == "boolean" then put(tostring(v))
	elseif t == "table" then
		local n = 0
		for _ in pairs(v) do n = n + 1 end
		local array = n > 0
		for i = 1, n do if v[i] == nil then array = false break end end
		if array then
			put("[")
			for i = 1, n do if i > 1 then put(",") end enc(v[i]) end
			put("]")
		else
			local keys = {}
			for k, x in pairs(v) do
				if type(x) ~= "function" then keys[#keys + 1] = type(k) == "number" and string.format("%d", k) or tostring(k) end
			end
			table.sort(keys)
			put("{")
			for i, k in ipairs(keys) do
				if i > 1 then put(",") end
				enc(k)
				put(":")
				local x = v[k]
				if x == nil then x = v[tonumber(k)] end
				enc(x)
			end
			put("}")
		end
	else put("null") end
end
enc(out)
io.write(table.concat(buf))
