-- Polestar's own scoring, run outside the game, to check wowstats/gear.py
-- against it (tests/test_gear_parity.py). Loads the release's ItemDB.lua (with
-- Polestar_ItemDB), Weights.lua, SpecData.lua, Adjust.lua and Gear.lua in an
-- empty environment with a stub client, then asks Gear.CompareToEquipped
-- for each item:
--
--   lua parity.lua <addon dir> < case.txt
--
-- case.txt, one fact per line:
--   class PRIEST 5          class file name and class ID
--   race Gnome 7 Alliance   race file name, race ID, faction
--   level 32
--   spec shadow             the Stat weights setting (default: auto)
--   adjust shadow sta x 1.5 an Adjust weights edit (x = multiple, v = value)
--   equip 16 1317           inventory slot and item ID worn
--   item 2042               an item to compare with what is worn
--
-- Prints one JSON object per line: {"item":..,"score":..,"delta":..,"pct":..,"slot":..,
-- "usable":..,"reason":..,"min_level":..,"equipped":..,"empty":..}, then
-- {"weights": {...}, "spec": key, "source": ...}. No bags, no talents, no
-- Forever Leveling BiS: the server has none of them either.

local addon = assert(arg[1], "usage: lua parity.lua <Polestar addon dir> < case.txt")

local deep
deep = setmetatable({}, { __index = function() return deep end, __call = function() return nil end })
local function noop() end

local case = { equip = {}, items = {}, adjust = {}, spec = "auto" }
for line in io.lines() do
	local w = {}
	for x in line:gmatch("%S+") do w[#w + 1] = x end
	if w[1] == "class" then case.class, case.classID = w[2], tonumber(w[3])
	elseif w[1] == "race" then case.race, case.raceID, case.faction = w[2], tonumber(w[3]), w[4]
	elseif w[1] == "level" then case.level = tonumber(w[2])
	elseif w[1] == "spec" then case.spec = w[2]
	elseif w[1] == "adjust" then
		case.adjust[w[2]] = case.adjust[w[2]] or {}
		case.adjust[w[2]][w[3]] = { [w[4]] = tonumber(w[5]) }
	elseif w[1] == "equip" then case.equip[tonumber(w[2])] = tonumber(w[3])
	elseif w[1] == "item" then case.items[#case.items + 1] = tonumber(w[2]) end
end

local settings = { gear = { spec = { [case.class] = case.spec } } }
local defaults = {}
local function lookup(t, path)
	for k in path:gmatch("[^.]+") do
		if type(t) ~= "table" then return nil end
		t = t[k]
	end
	return t
end
local db = { settings = settings, gearAdjust = { [case.class] = case.adjust } }

local ns = {
	Fire = noop, On = noop, OnLogin = noop, RegisterEvent = noop, RegisterCommand = noop, After = noop, Print = noop,
	Names = { Item = noop, Area = noop, Quest = noop, Npc = noop },
	L = setmetatable({}, { __index = function(_, k) return k end }),
	DB = function() return db end,
	RegisterDefaults = function(section, t)
		defaults[section] = defaults[section] or {}
		for k, v in pairs(t) do defaults[section][k] = v end
	end,
	Get = function(path)
		local v = lookup(settings, path)
		if v == nil then v = lookup(defaults, path) end
		return v
	end,
	Compat = {
		Readable = function(v) return v end,
		TalentConfigs = function() return {} end,
		GetSkillRank = function() return nil end,
		IsPlayerSpell = function() return false end,
		IsSpellKnown = function() return false end,
	},
}

local function env()
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
	local chunk, err
	if setfenv then
		chunk, err = loadstring(src, "@" .. path)
		if chunk then setfenv(chunk, env()) end
	else
		chunk, err = load(src, "@" .. path, "t", env())
	end
	if not chunk then error(err) end
	chunk("Polestar", ns)
end

local gear = addon .. "/Polestar/Gear/"
ns.Gear = { Scales = { FromSetting = function() return nil end } }
run(gear .. "ItemDB.lua")
run(addon .. "/Polestar_ItemDB/Items.lua")
run(addon .. "/Polestar_ItemDB/Forever.lua")
run(gear .. "Weights.lua")
run(gear .. "SpecData.lua")
run(gear .. "Adjust.lua")
run(gear .. "Gear.lua")
local G = ns.Gear

local C = G.Client
C.PlayerClass = function() return case.class, case.classID end
C.PlayerLevel = function() return case.level end
C.PlayerRace = function() return case.race, case.raceID, case.faction end
C.InventoryLink = function(slot) return case.equip[slot] and ("item:" .. case.equip[slot]) or nil end
C.ItemStats = function() return nil end -- the server has no client: ItemDB's stats only
C.ItemInfo = function() return nil end
C.BagSlots = function() return 0 end
C.NumSpecs = function() return nil end
C.SpecInfo = function() return nil end
C.TalentsLoaded = function() return nil end
C.PrimarySpec = function() return nil end
C.KnowsSpell = function() return false end
G.RefreshPlayer()

local function enc(v)
	local t = type(v)
	if v == nil then return "null" end
	if t == "number" then
		if math.floor(v) == v and math.abs(v) < 2^53 then return string.format("%d", v) end
		return string.format("%.17g", v)
	end
	if t == "boolean" then return tostring(v) end
	if t == "string" then return '"' .. v .. '"' end
	local keys = {}
	for k in pairs(v) do keys[#keys + 1] = tostring(k) end
	table.sort(keys)
	local parts = {}
	for _, k in ipairs(keys) do parts[#parts + 1] = '"' .. k .. '":' .. enc(v[k]) end
	return "{" .. table.concat(parts, ",") .. "}"
end

for _, id in ipairs(case.items) do
	local r = G.CompareToEquipped("item:" .. id)
	if r then
		print(enc({ item = id, score = r.score, delta = r.delta, pct = r.pct, slot = r.slot, usable = r.usable,
			reason = r.reason, min_level = r.minLevel, equipped = r.equipped, empty = r.empty }))
	else
		print(enc({ item = id }))
	end
end
local key, _, source = G.GetSpec()
print(enc({ weights = G.GetWeights(), spec = key, source = source }))
