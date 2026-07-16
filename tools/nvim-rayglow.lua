-- rayglow.nvim — push GLSL edits straight to the running RayGLow renderer.
--
-- Reference snippet (not loaded by the repo). Copy into your Neovim config, e.g.
-- ~/.config/nvim/lua/rayglow.lua, then in init.lua:
--
--     require("rayglow").setup({
--       ctl  = vim.fn.expand("~/Projects/rayglow/tools/rayglow_ctl.py"),
--       host = "192.168.2.113",       -- or set $RAYGLOW_HOST in your shell
--     })
--
-- On :w of any *.glsl it ships the buffer's shader to the Pi over the control
-- plane — bypassing mutagen, so the wall updates in <100ms — and surfaces the
-- renderer's GLSL compile errors right here via vim.notify.
--
-- Controls live under <leader>m ("matrix"); which-key guides the drill-down:
--   <leader>m n  next        <leader>m p  prev        <leader>m r  reload
--   <leader>m s  status      <leader>m <space>  play/pause    <leader>m u  push
--   <leader>m x  → scale …   (1/2/3/4 pick a supersample, a = auto)
--
-- Needs Neovim 0.10+ (vim.system) and python3 on PATH. rayglow_ctl.py is stdlib
-- only, so no venv is required.

local M = {}

local cfg = {
  ctl = nil,             -- path to tools/rayglow_ctl.py (required)
  python = 'python3',    -- rayglow_ctl.py is stdlib-only, no venv needed
  host = nil,            -- Pi host; nil => client uses $RAYGLOW_HOST or 127.0.0.1
  prefix = '<leader>m',  -- root of the control maps
  push_on_save = true,   -- BufWritePost *.glsl autocmd
  maps = true,           -- register the <prefix>… maps
  notify_ok = true,      -- toast on a successful push / control cmd
}

-- Run rayglow-ctl async; on nonzero exit, toast the error (the renderer's
-- compile message rides stderr). `on_ok` fires with the process result.
local function ctl(args, on_ok)
  local cmd = { cfg.python, cfg.ctl }
  if cfg.host then vim.list_extend(cmd, { '--host', cfg.host }) end
  vim.list_extend(cmd, args)
  vim.system(cmd, { text = true }, function(res)
    vim.schedule(function()
      if res.code == 0 then
        if on_ok then on_ok(res) end
      else
        local msg = (res.stderr ~= '' and res.stderr or res.stdout) or 'failed'
        vim.notify('rayglow: ' .. vim.trim(msg), vim.log.levels.ERROR)
      end
    end)
  end)
end

function M.push(file)
  file = file or vim.api.nvim_buf_get_name(0)
  ctl({ 'push', file }, function()
    if cfg.notify_ok then
      vim.notify('rayglow ← ' .. vim.fn.fnamemodify(file, ':t'),
        vim.log.levels.INFO)
    end
  end)
end

-- next / prev / reload / play / pause / status / scale …
function M.cmd(...)
  local args = { ... }
  ctl(args, function(res)
    if cfg.notify_ok and vim.trim(res.stdout) ~= '' then
      vim.notify('rayglow: ' .. vim.trim(res.stdout))
    end
  end)
end

-- One key for play/pause: read the current state, then flip it.
function M.toggle()
  ctl({ 'status' }, function(res)
    local ok, snap = pcall(vim.json.decode, vim.trim(res.stdout))
    M.cmd((ok and snap and snap.paused) and 'play' or 'pause')
  end)
end

function M.scale(v) M.cmd('scale', tostring(v)) end

function M.setup(opts)
  cfg = vim.tbl_extend('force', cfg, opts or {})
  assert(cfg.ctl, 'rayglow.setup: set ctl = path to tools/rayglow_ctl.py')

  vim.api.nvim_create_user_command('RayglowPush', function() M.push() end,
    { desc = 'push the current .glsl to the RayGLow wall' })
  vim.api.nvim_create_user_command('RayglowScale',
    function(a) M.scale(a.args) end,
    { nargs = 1, desc = 'set RayGLow supersample scale (1..8 or auto)' })

  if cfg.push_on_save then
    vim.api.nvim_create_autocmd('BufWritePost', {
      pattern = '*.glsl',
      callback = function(a) M.push(a.file) end,
      desc = 'push GLSL to the RayGLow wall',
    })
  end

  if cfg.maps then
    local p = cfg.prefix
    local function map(suffix, fn, desc)
      vim.keymap.set('n', p .. suffix, fn, { desc = 'rayglow ' .. desc })
    end
    map('n', function() M.cmd('next') end, 'next')
    map('p', function() M.cmd('prev') end, 'prev')
    map('r', function() M.cmd('reload') end, 'reload')
    map('s', function() M.cmd('status') end, 'status')
    map('<space>', M.toggle, 'play/pause')
    map('u', function() M.push() end, 'push (update) current')
    -- scale submenu: <prefix>x then 1/2/3/4 (supersample) or a (auto)
    for _, n in ipairs({ 1, 2, 3, 4 }) do
      map('x' .. n, function() M.scale(n) end, 'scale ' .. n .. 'x')
    end
    map('xa', function() M.scale('auto') end, 'scale auto')

    -- Optional which-key group labels (no-op if which-key isn't present).
    pcall(function()
      require('which-key').add({
        { p, group = 'rayglow' },
        { p .. 'x', group = 'scale' },
      })
    end)
  end
end

return M
