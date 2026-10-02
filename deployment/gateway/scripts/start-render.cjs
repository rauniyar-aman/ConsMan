const {spawnSync, spawn} = require('node:child_process');
const path = require('node:path');

async function main() {
  for (const key of ['DATABASE_URL', 'AUTH_SECRET', 'BASE_URL', 'GATEWAY_ADMIN_EMAIL', 'GATEWAY_ADMIN_PASSWORD']) {
    if (!process.env[key]) throw new Error(`Missing required setting: ${key}`);
  }
  if (process.env.AUTH_SECRET.length < 32 || process.env.GATEWAY_ADMIN_PASSWORD.length < 12) {
    throw new Error('Use a stable authentication secret of at least 32 characters and an administrator password of at least 12 characters.');
  }
  const database = new URL(process.env.DATABASE_URL);
  if (!['postgres:', 'postgresql:'].includes(database.protocol) || ['localhost', '127.0.0.1'].includes(database.hostname)) {
    throw new Error('Configure a dedicated hosted Gateway PostgreSQL database.');
  }
  const schema = spawnSync(process.execPath, [path.join('node_modules', 'prisma', 'build', 'index.js'), 'db', 'push', '--skip-generate'], {stdio:'inherit'});
  if (schema.status !== 0) throw new Error('Gateway schema initialization failed. No destructive schema override was used.');
  const {PrismaClient} = require('@prisma/client');
  const bcrypt = require('bcryptjs');
  const prisma = new PrismaClient();
  try {
    const email = process.env.GATEWAY_ADMIN_EMAIL.trim().toLowerCase();
    const existing = await prisma.user.findUnique({where:{email}});
    if (!existing) {
      await prisma.user.create({data:{email, name:'Office administrator', password:await bcrypt.hash(process.env.GATEWAY_ADMIN_PASSWORD, 12), role:'SUPERADMIN'}});
    } else if (existing.role !== 'SUPERADMIN') {
      throw new Error('Configured administrator already exists with another role; review it manually.');
    }
    await prisma.systemConfig.upsert({where:{id:'default'}, create:{id:'default', enableRegistration:false, timezone:'Asia/Kathmandu'}, update:{enableRegistration:false}});
  } finally {
    await prisma.$disconnect();
  }
  const child = spawn(process.execPath, [path.join('node_modules', 'tsx', 'dist', 'cli.mjs'), 'src/server/bootstrap.ts'], {stdio:'inherit', env:{...process.env, HOSTNAME:'0.0.0.0', DISABLE_KEEPALIVE:'true'}});
  for (const signal of ['SIGINT','SIGTERM']) process.on(signal, () => child.kill(signal));
  child.on('error', () => {console.error('Gateway server could not start.'); process.exitCode=1;});
  child.on('exit', code => {process.exitCode=code??1;});
}
main().catch(error => {console.error(error.message); process.exitCode=1;});
