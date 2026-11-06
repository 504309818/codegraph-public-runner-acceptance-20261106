#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/prctl.h>
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <linux/audit.h>
static int gate(void){
 const int denied[]={SYS_socket,SYS_socketpair,SYS_connect,SYS_bind,SYS_listen,SYS_accept,SYS_accept4,
 SYS_sendto,SYS_sendmsg,SYS_sendmmsg,SYS_recvfrom,SYS_recvmsg,SYS_recvmmsg,SYS_shutdown,
 SYS_getsockname,SYS_getpeername,SYS_getsockopt,SYS_setsockopt,SYS_io_uring_setup,SYS_io_uring_enter,
 SYS_io_uring_register,SYS_ptrace,SYS_process_vm_readv,SYS_process_vm_writev,SYS_pidfd_getfd,SYS_bpf};
 struct sock_filter f[96];unsigned n=0;
 f[n++]=(struct sock_filter)BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,arch));
 f[n++]=(struct sock_filter)BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AUDIT_ARCH_X86_64,1,0);
 f[n++]=(struct sock_filter)BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS);
 f[n++]=(struct sock_filter)BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,nr));
 f[n++]=(struct sock_filter)BPF_JUMP(BPF_JMP|BPF_JGE|BPF_K,0x40000000,0,1);
 f[n++]=(struct sock_filter)BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS);
 for(unsigned i=0;i<sizeof(denied)/sizeof(denied[0]);i++){
  f[n++]=(struct sock_filter)BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,denied[i],0,1);
  f[n++]=(struct sock_filter)BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|EPERM);
 }
 f[n++]=(struct sock_filter)BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW);
 struct sock_fprog p={.len=n,.filter=f};
 return prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0)||prctl(PR_SET_SECCOMP,SECCOMP_MODE_FILTER,&p);
}
static int exec_node(char *const args[],char *const env[]){
 execve(args[0],args,env);
 const int saved_errno=errno;
 const char *category=saved_errno==EACCES?"EACCES":saved_errno==ENOENT?"ENOENT":
  saved_errno==ENOEXEC?"ENOEXEC":saved_errno==EPERM?"EPERM":"OTHER";
 fprintf(stderr,"EXECVE_FAILED errno=%d category=%s\n",saved_errno,category);
 return 28;
}
int main(int argc,char **argv){
 if(argc!=1&&(argc!=2||strcmp(argv[1],"--index-only"))){return 26;}
 struct stat st;
 for(int i=0;i<3;i++)if(fstat(i,&st)||S_ISSOCK(st.st_mode)){fputs("STDIO_REJECTED\n",stderr);return 27;}
 if(syscall(SYS_close_range,3,~0U,0)||gate()){fputs("SECCOMP_UNAVAILABLE\n",stderr);return 27;}
 errno=0;if(socket(AF_INET,SOCK_STREAM,0)>=0||errno!=EPERM){fputs("SECCOMP_NOT_ENFORCED\n",stderr);return 27;}
 // Fresh anonymous pipes, never inherited host descriptors or sockets. Node's
 // default child stdio uses socketpair(), which remains forbidden by gate().
 for(int i=0;i<3;i++){
  int pair[2];
  if(pipe2(pair,0)||pair[0]!=3+2*i||pair[1]!=4+2*i){fputs("PIPE_SETUP_FAILED\n",stderr);return 29;}
 }
 char *args[]={"/opt/node","--liftoff-only","--disable-warning=ExperimentalWarning","/opt/worker.cjs",argc==2?"--index-only":NULL,NULL};
 char *env[]={"HOME=/tmp","LANG=C.UTF-8","CODEGRAPH_NO_DAEMON=1","CODEGRAPH_NO_WATCH=1",
 "CODEGRAPH_NO_DOWNLOAD=1","CODEGRAPH_NO_UPDATE_CHECK=1","CODEGRAPH_TELEMETRY=0","DO_NOT_TRACK=1",
 "CODEGRAPH_MCP_TOOLS=explore,status,search","UV_USE_IO_URING=0",NULL};
 fputs("SECCOMP_ENFORCED\n",stderr);return exec_node(args,env);
}
