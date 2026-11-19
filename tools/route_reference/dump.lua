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

local function env()
  local ns = stubs({ Nav = stubs({ RegisterTravelData = function(d) out.travel = d end }) })
  local e = {
    Polestar = stubs({
      RegisterGuide = function(g) out.guides[#out.guides + 1] = g end,
      RegisterLegs = function(_, _, list)
        for id, steps in pairs(list) do
          out.legs[id] = out.legs[id] or {}
          for k, v in pairs(steps) do out.legs[id][string.format("%d", k)] = v end
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

local function run(path)
  local f = io.open(path, "rb")
  if not f then return false end
  local src = f:read("*a")
  f:close()
  if src:sub(1, 1) == "\27" then error(path .. ": precompiled chunk refused") end
  local e, ns = env()
  local chunk, err
  if setfenv then
    chunk, err = loadstring(src, "@" .. path)
    if chunk then setfenv(chunk, e) end
  else
    chunk, err = load(src, "@" .. path, "t", e)
  end
  if not chunk then error(err) end
  chunk("Polestar", ns)
  return true
end

local function each_dir()
  -- No io.popen: the folder names are fixed in Polestar's layout.
  return { "Polestar_Guides", "Polestar_Guides_Alliance", "Polestar_Guides_Horde" }
end

if mode == "legs" then
  for _, r in ipairs({ "Dwarf", "Gnome", "Human", "NightElf", "Orc", "Scourge", "Skyborne", "Tauren", "Troll" }) do
    run(addon .. "/Polestar_Guides_" .. r .. "/Legs.lua")
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
