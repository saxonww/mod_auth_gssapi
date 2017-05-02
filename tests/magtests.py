#!/usr/bin/python
# Copyright (C) 2015 - mod_auth_gssapi contributors, see COPYING for license.

import argparse
import glob
import os
import random
import shutil
import signal
from string import Template
import subprocess
import sys
import time

# check that we can import requests (for use in test scripts)
import requests, requests_kerberos
del(requests)
del(requests_kerberos)

def parse_args():
    parser = argparse.ArgumentParser(description='Mod Auth GSSAPI Tests Environment')
    parser.add_argument('--path', default='%s/scratchdir' % os.getcwd(),
                        help="Directory in which tests are run")
    parser.add_argument('--so-dir', default='%s/src/.libs' % os.getcwd(),
                        help="mod_auth_gssapi shared object dirpath")

    return vars(parser.parse_args())


WRAP_HOSTNAME = "kdc.mag.dev"
WRAP_ALIASNAME = "alias.mag.dev"
WRAP_FAILNAME = "fail.mag.dev"
WRAP_IPADDR = '127.0.0.9'
WRAP_HTTP_PORT = '80'
WRAP_PROXY_PORT = '8080'

def setup_wrappers(base):

    pkgcfg = subprocess.Popen(['pkg-config', '--exists', 'socket_wrapper'])
    pkgcfg.wait()
    if pkgcfg.returncode != 0:
        raise ValueError('Socket Wrappers not available')

    pkgcfg = subprocess.Popen(['pkg-config', '--exists', 'nss_wrapper'])
    pkgcfg.wait()
    if pkgcfg.returncode != 0:
        raise ValueError('Socket Wrappers not available')

    wrapdir = os.path.join(base, 'wrapdir')
    if not os.path.exists(wrapdir):
        os.makedirs(wrapdir)

    hosts_file = os.path.join(testdir, 'hosts')
    with open(hosts_file, 'w+') as f:
        f.write('%s %s\n' % (WRAP_IPADDR, WRAP_HOSTNAME))
        f.write('%s %s\n' % (WRAP_IPADDR, WRAP_ALIASNAME))
        f.write('%s %s\n' % (WRAP_IPADDR, WRAP_FAILNAME))

    wenv = {'LD_PRELOAD': 'libsocket_wrapper.so libnss_wrapper.so',
            'SOCKET_WRAPPER_DIR': wrapdir,
            'SOCKET_WRAPPER_DEFAULT_IFACE': '9',
            'WRAP_PROXY_PORT': WRAP_PROXY_PORT,
            'NSS_WRAPPER_HOSTNAME': WRAP_HOSTNAME,
            'NSS_WRAPPER_HOSTS': hosts_file}

    return wenv


TESTREALM = "MAG.DEV"
KDC_DBNAME = 'db.file'
KDC_STASH = 'stash.file'
KDC_PASSWORD = 'modauthgssapi'
KRB5_CONF_TEMPLATE = '''
[libdefaults]
  default_realm = ${TESTREALM}
  dns_lookup_realm = false
  dns_lookup_kdc = false
  rdns = false
  ticket_lifetime = 24h
  forwardable = yes
  default_ccache_name = FILE://${TESTDIR}/ccaches/krb5_ccache_XXXXXX

[realms]
  ${TESTREALM} = {
    kdc =${WRAP_HOSTNAME}
  }

[domain_realm]
  .mag.dev = ${TESTREALM}
  mag.dev = ${TESTREALM}

[dbmodules]
  ${TESTREALM} = {
    database_name = ${KDCDIR}/${KDC_DBNAME}
  }
'''
KDC_CONF_TEMPLATE = '''
[kdcdefaults]
 kdc_ports = 88
 kdc_tcp_ports = 88
 restrict_anonymous_to_tgt = true

[realms]
 ${TESTREALM} = {
  master_key_type = aes256-cts
  max_life = 7d
  max_renewable_life = 14d
  acl_file = ${KDCDIR}/kadm5.acl
  dict_file = /usr/share/dict/words
  default_principal_flags = +preauth
  admin_keytab = ${TESTREALM}/kadm5.keytab
  key_stash_file = ${KDCDIR}/${KDC_STASH}
 }
[logging]
  kdc = FILE:${KDCLOG}
'''


def setup_kdc(testdir, wrapenv):

    # setup kerberos environment
    testlog = os.path.join(testdir, 'kerb.log')
    krb5conf = os.path.join(testdir, 'krb5.conf')
    kdcconf = os.path.join(testdir, 'kdc.conf')
    kdcdir = os.path.join(testdir, 'kdc')
    kdcstash = os.path.join(kdcdir, KDC_STASH)
    kdcdb = os.path.join(kdcdir, KDC_DBNAME)
    if os.path.exists(kdcdir):
        shutil.rmtree(kdcdir)
    os.makedirs(kdcdir)

    t = Template(KRB5_CONF_TEMPLATE)
    text = t.substitute({'TESTREALM': TESTREALM,
                         'TESTDIR': testdir,
                         'KDCDIR': kdcdir,
                         'KDC_DBNAME': KDC_DBNAME,
                         'WRAP_HOSTNAME': WRAP_HOSTNAME})
    with open(krb5conf, 'w+') as f:
        f.write(text)

    t = Template(KDC_CONF_TEMPLATE)
    text = t.substitute({'TESTREALM': TESTREALM,
                         'KDCDIR': kdcdir,
                         'KDCLOG': testlog,
                         'KDC_STASH': KDC_STASH})
    with open(kdcconf, 'w+') as f:
        f.write(text)

    kdcenv = {'PATH': '/sbin:/bin:/usr/sbin:/usr/bin',
              'KRB5_CONFIG': krb5conf,
              'KRB5_KDC_PROFILE': kdcconf,
              'KRB5_TRACE': os.path.join(testdir, 'krbtrace.log')}
    kdcenv.update(wrapenv)

    with (open(testlog, 'a')) as logfile:
        ksetup = subprocess.Popen(["kdb5_util", "create", "-W", "-s",
                                   "-r", TESTREALM, "-P", KDC_PASSWORD],
                                  stdout=logfile, stderr=logfile,
                                  env=kdcenv, preexec_fn=os.setsid)
    ksetup.wait()
    if ksetup.returncode != 0:
        raise ValueError('KDC Setup failed')

    with (open(testlog, 'a')) as logfile:
        kdcproc = subprocess.Popen(['krb5kdc', '-n'],
                                   stdout=logfile, stderr=logfile,
                                   env=kdcenv, preexec_fn=os.setsid)

    return kdcproc, kdcenv


def kadmin_local(cmd, env, logfile):
    ksetup = subprocess.Popen(["kadmin.local", "-q", cmd],
                              stdout=logfile, stderr=logfile,
                              env=env, preexec_fn=os.setsid)
    ksetup.wait()
    if ksetup.returncode != 0:
        raise ValueError('Kadmin local [%s] failed' % cmd)


USR_NAME = "maguser"
USR_PWD = "magpwd"
USR_NAME_2 = "maguser2"
USR_PWD_2 = "magpwd2"
SVC_KTNAME = "httpd/http.keytab"
KEY_TYPE = "aes256-cts-hmac-sha1-96:normal"


def setup_keys(tesdir, env):

    testlog = os.path.join(testdir, 'kerb.log')

    svc_name = "HTTP/%s" % WRAP_HOSTNAME
    svc_keytab = os.path.join(testdir, SVC_KTNAME)
    cmd = "addprinc -randkey -e %s %s" % (KEY_TYPE, svc_name)
    with (open(testlog, 'a')) as logfile:
        kadmin_local(cmd, env, logfile)
    cmd = "ktadd -k %s -e %s %s" % (svc_keytab, KEY_TYPE, svc_name)
    with (open(testlog, 'a')) as logfile:
        kadmin_local(cmd, env, logfile)

    cmd = "addprinc -pw %s -e %s %s" % (USR_PWD, KEY_TYPE, USR_NAME)
    with (open(testlog, 'a')) as logfile:
        kadmin_local(cmd, env, logfile)

    cmd = "addprinc -pw %s -e %s %s" % (USR_PWD_2, KEY_TYPE, USR_NAME_2)
    with (open(testlog, 'a')) as logfile:
        kadmin_local(cmd, env, logfile)

    # alias for multinamed hosts testing
    alias_name = "HTTP/%s" % WRAP_ALIASNAME
    cmd = "addprinc -randkey -e %s %s" % (KEY_TYPE, alias_name)
    with (open(testlog, 'a')) as logfile:
        kadmin_local(cmd, env, logfile)
    cmd = "ktadd -k %s -e %s %s" % (svc_keytab, KEY_TYPE, alias_name)
    with (open(testlog, 'a')) as logfile:
        kadmin_local(cmd, env, logfile)

    keys_env = { "KRB5_KTNAME": svc_keytab }
    keys_env.update(env)

    return keys_env


def setup_http(testdir, so_dir, wrapenv):

    httpdir = os.path.join(testdir, 'httpd')
    if os.path.exists(httpdir):
        shutil.rmtree(httpdir)
    os.makedirs(httpdir)
    os.mkdir(os.path.join(httpdir, 'conf.d'))
    os.mkdir(os.path.join(httpdir, 'html'))
    os.mkdir(os.path.join(httpdir, 'logs'))

    distro = "Fedora"
    moddir = "/etc/httpd/modules"
    if not os.path.exists(moddir):
        distro = "Debian"
        moddir = "/usr/lib/apache2/modules"
    if not os.path.exists(moddir):
        raise ValueError("Could not find Apache module directory!")
    os.symlink(moddir, os.path.join(httpdir, 'modules'))

    shutil.copy('%s/mod_auth_gssapi.so' % so_dir, httpdir)

    with open('tests/httpd.conf') as f:
        t = Template(f.read())
        text = t.substitute({'HTTPROOT': httpdir,
                             'HTTPNAME': WRAP_HOSTNAME,
                             'HTTPADDR': WRAP_IPADDR,
                             'PROXYPORT': WRAP_PROXY_PORT,
                             'HTTPPORT': WRAP_HTTP_PORT})
    config = os.path.join(httpdir, 'httpd.conf')
    with open(config, 'w+') as f:
        f.write(text)

    shutil.copy('tests/401.html', os.path.join(httpdir, 'html'))

    httpenv = {'PATH': '/sbin:/bin:/usr/sbin:/usr/bin',
               'MALLOC_CHECK_': '3',
               'MALLOC_PERTURB_': str(random.randint(0, 32767) % 255 + 1)}
    httpenv.update(wrapenv)

    httpd = "httpd" if distro == "Fedora" else "apache2"
    httpproc = subprocess.Popen([httpd, '-DFOREGROUND', '-f', config],
                                env=httpenv, preexec_fn=os.setsid)

    return httpproc


def kinit_user(testdir, kdcenv):
    testlog = os.path.join(testdir, 'kinit.log')
    ccache = os.path.join(testdir, 'k5ccache')
    testenv = {'KRB5CCNAME': ccache}
    testenv.update(kdcenv)

    with (open(testlog, 'a')) as logfile:
        kinit = subprocess.Popen(["kinit", USR_NAME],
                                 stdin=subprocess.PIPE,
                                 stdout=logfile, stderr=logfile,
                                 env=testenv, preexec_fn=os.setsid)
        kinit.communicate('%s\n' % USR_PWD)
        kinit.wait()
        if kinit.returncode != 0:
            raise ValueError('kinit failed')
    return testenv


def test_spnego_auth(testdir, testenv, testlog):

    spnegodir = os.path.join(testdir, 'httpd', 'html', 'spnego')
    os.mkdir(spnegodir)
    shutil.copy('tests/index.html', spnegodir)

    with (open(testlog, 'a')) as logfile:
        spnego = subprocess.Popen(["tests/t_spnego.py"],
                                  stdout=logfile, stderr=logfile,
                                  env=testenv, preexec_fn=os.setsid)
        spnego.wait()
        if spnego.returncode != 0:
            sys.stderr.write('SPNEGO: FAILED\n')
        else:
            sys.stderr.write('SPNEGO: SUCCESS\n')

    with (open(testlog, 'a')) as logfile:
        spnego = subprocess.Popen(["tests/t_spnego_proxy.py"],
                                  stdout=logfile, stderr=logfile,
                                  env=testenv, preexec_fn=os.setsid)
        spnego.wait()
        if spnego.returncode != 0:
            sys.stderr.write('SPNEGO Proxy Auth: FAILED\n')
        else:
            sys.stderr.write('SPNEGO Proxy Auth: SUCCESS\n')

    with (open(testlog, 'a')) as logfile:
        spnego = subprocess.Popen(["tests/t_spnego_no_auth.py"],
                                  stdout=logfile, stderr=logfile,
                                  env=testenv, preexec_fn=os.setsid)
        spnego.wait()
        if spnego.returncode != 0:
            sys.stderr.write('SPNEGO No Auth: FAILED\n')
        else:
            sys.stderr.write('SPNEGO No Auth: SUCCESS\n')


def test_spnego_rewrite(testdir, testenv, testlog):

    spnego_rewrite_dir = os.path.join(testdir, 'httpd', 'html',
                                          'spnego_rewrite')
    os.mkdir(spnego_rewrite_dir)
    shutil.copy('tests/index.html', spnego_rewrite_dir)

    with (open(testlog, 'a')) as logfile:
        spnego = subprocess.Popen(["tests/t_spnego_rewrite.py"],
                                  stdout=logfile, stderr=logfile,
                                  env=testenv, preexec_fn=os.setsid)
        spnego.wait()
        if spnego.returncode != 0:
            sys.stderr.write('SPNEGO Rewrite: FAILED\n')
        else:
            sys.stderr.write('SPNEGO Rewrite: SUCCESS\n')


def test_spnego_negotiate_once(testdir, testenv, testlog):

    spnego_negotiate_once_dir = os.path.join(testdir, 'httpd', 'html',
                                          'spnego_negotiate_once')
    os.mkdir(spnego_negotiate_once_dir)
    shutil.copy('tests/index.html', spnego_negotiate_once_dir)

    with (open(testlog, 'a')) as logfile:
        spnego = subprocess.Popen(["tests/t_spnego_negotiate_once.py"],
                                  stdout=logfile, stderr=logfile,
                                  env=testenv, preexec_fn=os.setsid)
        spnego.wait()
        if spnego.returncode != 0:
            sys.stderr.write('SPNEGO Negotiate Once: FAILED\n')
        else:
            sys.stderr.write('SPNEGO Negotiate Once: SUCCESS\n')


def test_basic_auth_krb5(testdir, testenv, testlog):

    basicdir = os.path.join(testdir, 'httpd', 'html', 'basic_auth_krb5')
    os.mkdir(basicdir)
    shutil.copy('tests/index.html', basicdir)

    with (open(testlog, 'a')) as logfile:
        basick5 = subprocess.Popen(["tests/t_basic_k5.py"],
                                   stdout=logfile, stderr=logfile,
                                   env=testenv, preexec_fn=os.setsid)
        basick5.wait()
        if basick5.returncode != 0:
            sys.stderr.write('BASIC-AUTH: FAILED\n')
        else:
            sys.stderr.write('BASIC-AUTH: SUCCESS\n')

    with (open(testlog, 'a')) as logfile:
        basick5 = subprocess.Popen(["tests/t_basic_k5_two_users.py"],
                                   stdout=logfile, stderr=logfile,
                                   env=testenv, preexec_fn=os.setsid)
        basick5.wait()
        if basick5.returncode != 0:
            sys.stderr.write('BASIC-AUTH Two Users: FAILED\n')
        else:
            sys.stderr.write('BASIC-AUTH Two Users: SUCCESS\n')

    with (open(testlog, 'a')) as logfile:
        basick5 = subprocess.Popen(["tests/t_basic_k5_fail_second.py"],
                                   stdout=logfile, stderr=logfile,
                                   env=testenv, preexec_fn=os.setsid)
        basick5.wait()
        if basick5.returncode != 0:
            sys.stderr.write('BASIC Fail Second User: FAILED\n')
        else:
            sys.stderr.write('BASIC Fail Second User: SUCCESS\n')

    with (open(testlog, 'a')) as logfile:
        basick5 = subprocess.Popen(["tests/t_basic_proxy.py"],
                                   stdout=logfile, stderr=logfile,
                                   env=testenv, preexec_fn=os.setsid)
        basick5.wait()
        if basick5.returncode != 0:
            sys.stderr.write('BASIC Proxy Auth: FAILED\n')
        else:
            sys.stderr.write('BASIC Proxy Auth: SUCCESS\n')


def test_bad_acceptor_name(testdir, testenv, testlog):

    bandir = os.path.join(testdir, 'httpd', 'html', 'bad_acceptor_name')
    os.mkdir(bandir)
    shutil.copy('tests/index.html', bandir)

    with (open(testlog, 'a')) as logfile:
        ban = subprocess.Popen(["tests/t_bad_acceptor_name.py"],
                               stdout=logfile, stderr=logfile,
                               env=testenv, preexec_fn=os.setsid)
        ban.wait()
        if ban.returncode != 0:
            sys.stderr.write('BAD ACCEPTOR: SUCCESS\n')
        else:
            sys.stderr.write('BAD ACCEPTOR: FAILED\n')


def test_no_negotiate(testdir, testenv, testlog):

    nonego_dir = os.path.join(testdir, 'httpd', 'html', 'nonego')
    os.mkdir(nonego_dir)
    shutil.copy('tests/index.html', nonego_dir)

    with (open(testlog, 'a')) as logfile:
        spnego = subprocess.Popen(["tests/t_nonego.py"],
                                  stdout=logfile, stderr=logfile,
                                  env=testenv, preexec_fn=os.setsid)
        spnego.wait()
        if spnego.returncode != 0:
            sys.stderr.write('NO Negotiate: FAILED\n')
        else:
            sys.stderr.write('NO Negotiate: SUCCESS\n')


def test_hostname_acceptor(testdir, testenv, testlog):

    hdir = os.path.join(testdir, 'httpd', 'html', 'hostname_acceptor')
    os.mkdir(hdir)
    shutil.copy('tests/index.html', hdir)

    with (open(testlog, 'a')) as logfile:
        failed = False
        for (name, fail) in [(WRAP_HOSTNAME, False),
                             (WRAP_ALIASNAME,False),
                             (WRAP_FAILNAME, True)]:
            res = subprocess.Popen(["tests/t_hostname_acceptor.py", name],
                                   stdout=logfile, stderr=logfile,
                                   env=testenv, preexec_fn=os.setsid)
            res.wait()
            if fail:
                if res.returncode == 0:
                    failed = True
            else:
                if res.returncode != 0:
                    failed = True
            if failed:
                break

        if failed:
            sys.stderr.write('HOSTNAME ACCEPTOR: FAILED\n')
        else:
            sys.stderr.write('HOSTNAME ACCEPTOR: SUCCESS\n')


if __name__ == '__main__':

    args = parse_args()

    testdir = args['path']
    so_dir = args['so_dir']
    if os.path.exists(testdir):
        shutil.rmtree(testdir)
    os.makedirs(testdir)

    processes = dict()

    testlog = os.path.join(testdir, 'tests.log')

    try:
        wrapenv = setup_wrappers(testdir)

        kdcproc, kdcenv = setup_kdc(testdir, wrapenv)
        processes['KDC(%d)' % kdcproc.pid] = kdcproc

        httpproc = setup_http(testdir, so_dir, kdcenv)
        processes['HTTPD(%d)' % httpproc.pid] = httpproc

        keysenv = setup_keys(testdir, kdcenv)
        testenv = kinit_user(testdir, kdcenv)

        testenv['DELEGCCACHE'] = os.path.join(testdir, 'httpd',
                                              USR_NAME + '@' + TESTREALM)
        test_spnego_auth(testdir, testenv, testlog)

        testenv['MAG_GSS_NAME'] = USR_NAME + '@' + TESTREALM
        test_spnego_rewrite(testdir, testenv, testlog)

        test_spnego_negotiate_once(testdir, testenv, testlog)

        test_hostname_acceptor(testdir, testenv, testlog)

        test_bad_acceptor_name(testdir, testenv, testlog)

        testenv = {'MAG_USER_NAME': USR_NAME,
                   'MAG_USER_PASSWORD': USR_PWD,
                   'MAG_USER_NAME_2': USR_NAME_2,
                   'MAG_USER_PASSWORD_2': USR_PWD_2}
        testenv.update(kdcenv)
        test_basic_auth_krb5(testdir, testenv, testlog)

        test_no_negotiate(testdir, testenv, testlog)

    finally:
        with (open(testlog, 'a')) as logfile:
            for name in processes:
                logfile.write("Killing %s\n" % name)
                os.killpg(processes[name].pid, signal.SIGTERM)
