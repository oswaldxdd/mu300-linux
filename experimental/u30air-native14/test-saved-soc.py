"""Exhaustively check Android/Linux integer and fractional persistence."""
from pathlib import Path
import subprocess as sp
import tempfile

root = Path(__file__).resolve().parent
code = r'''
#include <assert.h>
#include "fgu-soc-policy.h"
int main(void) {
 for (unsigned int tenth=0; tenth<=1000; ++tenth) {
  unsigned int raw=tenth/10 | (tenth%10)<<8;
  assert(u30_saved_soc(raw)==(int)((tenth+5)/10));
 }
 for (unsigned int integer=0; integer<=100; ++integer)
  assert(u30_saved_soc(integer)==(int)integer);
 for (unsigned int raw=0; raw<4096; ++raw) {
  unsigned int integer=raw&255, decimal=raw>>8;
  if (integer>100 || decimal>9 || (integer==100 && decimal))
   assert(u30_saved_soc(raw)==-1);
 }
 assert(u30_saved_soc(85)==85);
 assert(u30_saved_soc(85 | (1<<8))==85);
 assert(u30_saved_soc(85 | (6<<8))==86);
}
'''
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp)/'test.c'; p.write_text(code); exe=Path(tmp)/'test'
    sp.run(['gcc','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(root/'source'),str(p),'-o',str(exe)],check=True)
    sp.run([str(exe)],check=True)
print('PASS: all Android tenth-percent encodings, Linux integers and invalid fields')
