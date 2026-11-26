-- Reads Polestar's compiled addon files (Guides.lua, MapFrames.lua,
-- TravelData.lua) and prints what they register as one JSON object:
-- {guides = [...], frames = {era = ...}, travel = {...}}. With `legs`, the
-- baked walks of the race parts instead: {legs = {[guide] = {[step] = leg}}}.
--
--   lua dump.lua <addon dir> [legs]
--
-- The files are data from a download: each runs in an empty environment
-- (no os, io, load, require), as text only, with stub registries. Works on
-- Lua 5.1 to 5.4.

local addon = assert(arg[1], "usage: lua dump.lua <Polestar addon dir>")

local mode = arg[2] or "guides"
local out = { guides = {}, frames = {}, travel = nil, legs = {} }

-- Anything else the files call or index is a no-op that can be indexed further.
local deep
deep = setmetatable({}, { __index = function() return deep end, __call = function() return nil end })
local function stubs(t)
  return setmetatable(t, { __index = function() return deep end })
end

local function text_chunk(src, name, globals)
  if src:sub(1, 1) == "\27" then error(name .. ": precompiled chunk refused") end
  if setfenv then
    local chunk = assert(loadstring(src, "@" .. name))
    setfenv(chunk, globals)
    return chunk
  end
  return assert(load(src, "@" .. name, "t", globals))
end

local function register_guide(g)
  -- Pack format 8 stores unopened guides as Lua data returned from an empty environment.
  if g.stepsSrc then
    g.steps = text_chunk(g.stepsSrc, g.id, {})()
    assert(type(g.steps) == "table" and #g.steps == g.stepCount, g.id .. ": wrong deferred step count")
    g.stepsSrc = nil
  end
  out.guides[#out.guides + 1] = g
end

local function env(race, state)
  local ns = stubs({ Nav = stubs({ RegisterTravelData = function(d) out.travel = d end }) })
  local e = {
    Polestar = stubs({
      IsPlayerRace = function(r) return r == race end,
      RegisterGuide = register_guide,
      RegisterLegs = function(_, _, list)
        for id, steps in pairs(list) do
          out.legs[id] = out.legs[id] or {}
          for k, v in pairs(steps) do
            out.legs[id][string.format("%d", k)] = v
            state.legs = state.legs + 1
          end
        end
      end,
      RegisterMapFrames = function(f)
        for set, maps in pairs(f) do
          out.frames[set] = out.frames[set] or {}
          for k, v in pairs(maps) do out.frames[set][k] = v end
        end
      end,
    }),
    pairs = pairs, ipairs = ipairs, next = next, type = type, tostring = tostring, tonumber = tonumber,
    select = select, unpack = unpack or table.unpack,
    string = { format = string.format, sub = string.sub, rep = string.rep },
    table = { insert = table.insert, concat = table.concat },
    math = { floor = math.floor, max = math.max, min = math.min },
  }
  e._G = e
  return e, ns
end

local function run(path, race)
  local f = io.open(path, "rb")
  if not f then return false end
  local src = f:read("*a")
  f:close()
  local state = { legs = 0 }
  local e, ns = env(race, state)
  text_chunk(src, path, e)("Polestar", ns)
  if mode == "legs" then assert(state.legs > 0, path .. " registered no travel legs") end
  return true
end

local function each_dir()
  -- No io.popen: the folder names are fixed in Polestar's layout.
  return { "Polestar_Guides", "Polestar_Guides_Alliance", "Polestar_Guides_Horde" }
end

if mode == "legs" then
  local factions = {
    Alliance = { "Dwarf", "Gnome", "Human", "NightElf", "Skyborne" },
    Horde = { "Orc", "Scourge", "Skyborne", "Tauren", "Troll" },
  }
  local paths, loaded = {}, 0
  for _, faction in ipairs({ "Alliance", "Horde" }) do
    for _, race in ipairs(factions[faction]) do
      local path = addon .. "/Polestar_Guides_" .. faction .. "/Legs_" .. race .. ".lua"
      if run(path, race) then loaded = loaded + 1 else paths[#paths + 1] = path end
    end
  end
  if loaded == 0 then
    -- Before 0.4.3 both Skyborne variants occupied the same race pack.
    for _, race in ipairs({ "Dwarf", "Gnome", "Human", "NightElf", "Orc", "Scourge", "Skyborne", "Tauren", "Troll" }) do
      local path = addon .. "/Polestar_Guides_" .. race .. "/Legs.lua"
      assert(run(path, race), path .. " missing")
    end
  elseif #paths > 0 then
    error("Missing faction travel legs: " .. table.concat(paths, ", "))
  end
  out = { legs = out.legs }
else
  for _, d in ipairs(each_dir()) do
    run(addon .. "/" .. d .. "/MapFrames.lua")
    assert(run(addon .. "/" .. d .. "/Guides.lua"), addon .. "/" .. d .. "/Guides.lua missing")
  end
  assert(run(addon .. "/Polestar/Nav/TravelData.lua"), "TravelData.lua missing")
  out.legs = nil
end

-- JSON out.
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
      put("{")
      local first = true
      for k, x in pairs(v) do
        if type(x) ~= "function" then
          if not first then put(",") end
          first = false
          enc(type(k) == "number" and string.format("%d", k) or tostring(k))
          put(":")
          enc(x)
        end
      end
      put("}")
    end
  else put("null") end
end
enc(out)
io.write(table.concat(buf))
