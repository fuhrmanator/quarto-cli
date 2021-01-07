

# don't print entire python stack for cell execution error (in oneshot mode)

# provide a setup chunk for julia

# logging/error handling

# use oneshot on windows (or consider port/secret implementation)

# domain sockets per unique render target path
   # temp file
   # set user only permissions on the domain socket


import os
import atexit
import random
import copy
import sys
import json
import logging
import pprint
import daemon
from pathlib import Path

import nbformat
from nbclient import NotebookClient

from socketserver import TCPServer, StreamRequestHandler

# optional import of papermill for params support
try:
   from papermill import translators as papermill_translate
except ImportError:
   papermill_translate = None

# optional import of jupyter-cache
try:
   from jupyter_cache import get_cache
except ImportError:
   get_cache = None

NB_FORMAT_VERSION = 4

def notebook_init(nb, resources, allow_errors):

   if not hasattr(notebook_init, "client"):
      
      # create notebook client
      client = NotebookClient(nb, resources = resources)
      client.allow_errors = allow_errors
      client.record_timing = False
      client.create_kernel_manager()
      client.start_new_kernel()
      client.start_new_kernel_client()
      info_msg = client.wait_for_reply(client.kc.kernel_info())
      client.nb.metadata['language_info'] = info_msg['content']['language_info']
      notebook_init.client = client

      # cleanup kernel at process exit
      atexit.register(client._cleanup_kernel)
      
   else:
      # if the kernel has changed we need to force a restart
      if nb.metadata.kernelspec.name != notebook_init.client.nb.metadata.kernelspec.name:
         raise RestartKernel

      # set the new notebook, resources, etc.
      notebook_init.client.nb = nb
      notebook_init.client.resources = resources
      notebook_init.client.allow_errors = allow_errors

   return notebook_init.client


def notebook_execute(options, status):

   # unpack options
   input = options["target"]["input"]
   format = options["format"]
   resource_dir = options["resourceDir"]
   params = options.get("params", None)
   run_path = options.get("cwd", "")
   quiet = options.get('quiet', False)

   # change working directory and strip dir off of paths
   os.chdir(Path(input).parent)
   input = Path(input).name

   # read variables out of format
   execute = format["execution"]

   allow_errors = bool(execute["allow-errors"])
   fig_width = execute["fig-width"]
   fig_height = execute["fig-height"]
   fig_format = execute["fig-format"]
   fig_dpi = execute["fig-dpi"]
   if "cache" in execute:
      cache = execute["cache"]
   else:
      cache = "user"

   # set environment variables
   os.environ["JUPYTER_FIG_WIDTH"] = str(fig_width)
   os.environ["JUPYTER_FIG_HEIGHT"] = str(fig_height)

   # read the notebook
   nb = nbformat.read(input, as_version = NB_FORMAT_VERSION)

   # inject parameters if provided
   if params:
      nb_parameterize(nb, params)

   # insert setup cell
   setup_cell = nb_setup_cell(nb.metadata.kernelspec, resource_dir, fig_width, fig_height, fig_format, fig_dpi)
   nb.cells.insert(0, setup_cell)

   # are we using the cache, if so connect to the cache, and then if we aren't in 'refresh'
   # (forced re-execution) mode then try to satisfy the execution request from the cache
   if cache == True or cache == "refresh":
      if not get_cache:
          raise ImportError('The jupyter-cache package is required for cached execution')
      nb_cache = get_cache(".jupyter_cache")
      if not cache == "refresh":
         cached_nb = nb_from_cache(nb, nb_cache)
         if cached_nb:
            cached_nb.cells.pop(0)
            nb_write(cached_nb, input)
            status("(Notebook read from cache)\n\n")
            return
   else:
      nb_cache = None
      
   # create resources for execution
   resources = dict()
   if run_path:
      resources["metadata"] = { "path": run_path }

   # create NotebookClient
   client = notebook_init(nb, resources, allow_errors)
      
   # compute total code cells (for progress)
   current_code_cell = 1
   total_code_cells = sum(cell.cell_type == 'code' for cell in client.nb.cells)

   # execute the cells
   for index, cell in enumerate(client.nb.cells):
      # progress
      progress = cell.cell_type == 'code' and index > 0
      if progress:
         status("  Cell {0}/{1}...".format(
            current_code_cell- 1, total_code_cells - 1
         ))
         
      # clear cell output
      cell = cell_clear_output(cell)

      # execute cell
      cell = cell_execute(
         client, 
         cell, 
         index, 
         current_code_cell,
         index > 0 # add_to_history
      )

      # if this was the setup cell, see if we need to exit b/c dependencies are out of date
      if index == 0:
         kernel_deps = nb_kernel_depenencies(cell)
         if hasattr(notebook_execute, "kernel_deps"):
            for path in kernel_deps.keys():
               if path in notebook_execute.kernel_deps.keys():
                  if notebook_execute.kernel_deps[path] != kernel_deps[path]:
                     raise RestartKernel
               else:
                  notebook_execute.kernel_deps[path] = kernel_deps[path]
         else:
            notebook_execute.kernel_deps = kernel_deps

         # we are done w/ setup (with no restarts) so it's safe to print 'Executing...'
         status("\nExecuting '{0}'\n".format(input))

      # assign cell
      client.nb.cells[index] = cell

      # increment current code cell
      if cell.cell_type == 'code':
         current_code_cell += 1

      # end progress
      if progress:
         status("Done\n")

   # set widgets metadata   
   client.set_widgets_metadata()

   # write to the cache
   if nb_cache:
      nb_write(client.nb, input)
      nb_cache.cache_notebook_file(path = Path(input), overwrite = True)

   # remove setup cell
   client.nb.cells.pop(0)

   # re-write without setup cell
   nb_write(client.nb, input)

   # execute cleanup cell
   cleanup_cell = nb_cleanup_cell(nb.metadata.kernelspec, resource_dir)
   nb.cells.append(cleanup_cell)
   client.execute_cell(
      cell = cleanup_cell, 
      cell_index = len(client.nb.cells) - 1, 
      store_history = False
   )
   nb.cells.pop()

   # progress
   status("\n")

   # return flag indicating whether we should persist 
   persist = notebook_execute.kernel_deps != None
   return persist



def nb_write(nb, input):
   outputstr = nbformat.writes(nb, version = NB_FORMAT_VERSION)
   if not outputstr.endswith("\n"):
      outputstr = outputstr + "\n"

   # re-write contents back to input file
   with open(input, "w") as file:
      file.write(outputstr)


def nb_setup_cell(kernelspec, resource_dir, fig_width, fig_height, fig_format, fig_dpi):
   return nb_language_cell('setup', kernelspec, resource_dir, fig_width, fig_height, fig_format, fig_dpi)

def nb_cleanup_cell(kernelspec, resource_dir):
   return nb_language_cell('cleanup', kernelspec, resource_dir)

def nb_language_cell(name, kernelspec, resource_dir, *args):
   source = ''
   kLanguages = { 
      'python' : '.py'
   }
   kernelLanguage = kernelspec.language
   if kernelLanguage in kLanguages:
      path = os.path.join(resource_dir, 'jupyter', 'lang', name + kLanguages[kernelLanguage])
      with open(path, 'r') as file:
         source = file.read().format(*args)  

   # create cell
   return nbformat.versions[NB_FORMAT_VERSION].new_code_cell(
      source = source
   )

def nb_from_cache(nb, nb_cache, nb_meta = ("kernelspec", "language_info", "widgets")):
   try:
      cache_record = nb_cache.match_cache_notebook(nb)
      cache_bundle = nb_cache.get_cache_bundle(cache_record.pk)
      cache_nb = cache_bundle.nb
      nb = copy.deepcopy(nb)
      # selected (execution-oriented) metadata
      if nb_meta is None:
         nb.metadata = cache_nb.metadata
      else:
         for key in nb_meta:
            if key in cache_nb.metadata:
               nb.metadata[key] = cache_nb.metadata[key]
      # code cells
      for idx in range(len(nb.cells)):
         if nb.cells[idx].cell_type == "code":
            cache_cell = cache_nb.cells.pop(0)    
            nb.cells[idx] = cache_cell
      return nb
   except KeyError:
      return None

def nb_kernel_depenencies(cell):
   for index, output in enumerate(cell.outputs):
      if output.name == 'stdout' and output.output_type == 'stream':
         return json.loads(output.text)
   return None

def cell_execute(client, cell, index, execution_count, store_history):

   no_execute_tag = 'no-execute'
   allow_errors_tag = 'allow-errors'

   # ensure we have tags
   tags = cell.get('metadata', {}).get('tags', [])
     
   # execute unless the 'no-execute' tag is active
   if not no_execute_tag in tags:
      
      # if we see 'allow-errors' then add 'raises-exception'
      if allow_errors_tag in tags:
         if not "metadata" in cell:
            cell["metadata"] = {}
         cell["metadata"]["tags"] = tags + ['raises-exception'] 

      # execute
      cell = client.execute_cell(
         cell = cell, 
         cell_index = index, 
         execution_count = execution_count,
         store_history = store_history
      )
      
      # if lines_to_next_cell is 0 then fix it to be 1
      lines_to_next_cell = cell.get('metadata', {}).get('lines_to_next_cell', -1)
      if lines_to_next_cell == 0:
         cell["metadata"]["lines_to_next_cell"] = 1

      # remove injected raises-exception
      if allow_errors_tag in tags:
        cell["metadata"]["tags"].remove('raises-exception')

   # update execution count
   if cell.cell_type == 'code':
      cell.execution_count = execution_count

   # return cell
   return cell
   

def cell_clear_output(cell):
   remove_metadata = ['collapsed', 'scrolled']
   if cell.cell_type == 'code':
      cell.outputs = []
      cell.execution_count = None
      if 'metadata' in cell:
         for field in remove_metadata:
            cell.metadata.pop(field, None)
   return cell

def nb_parameterize(nb, params):

   # verify papermill import
   if not papermill_translate:
      raise ImportError('The papermill package is required for processing --execute-params')

   # Generate parameter content based on the kernel_name
   kernel_name = nb.metadata.kernelspec.name
   language = nb.metadata.kernelspec.language
   params_content = papermill_translate.translate_parameters(
      kernel_name, 
      language, 
      params, 
      'Injected Parameters'
   )

    # find params index and note any tags on it
   params_index = find_first_tagged_cell_index(nb, "parameters")
   if params_index != -1:
      params_cell_tags = nb.cells[params_index].get('metadata', {}).get('tags', []).copy()
      params_cell_tags.remove("parameters")
   else:
      params_cell_tags = []
      
   # create params cell
   params_cell = nbformat.v4.new_code_cell(source=params_content)
   params_cell.metadata['tags'] = ['injected-parameters'] + params_cell_tags

    # find existing injected params index
   injected_params_index = find_first_tagged_cell_index(nb, 'injected-parameters')

   # find the right insertion/replace point for the injected params
   if injected_params_index >= 0:
      # Replace the injected cell with a new version
      before = nb.cells[:injected_params_index]
      after = nb.cells[injected_params_index + 1 :]
   elif params_index >= 0:
      # Add an injected cell after the parameter cell
      before = nb.cells[: params_index + 1]
      after = nb.cells[params_index + 1 :]
   else:
      # Inject to the top of the notebook
      before = []
      after = nb.cells

   nb.cells = before + [params_cell] + after
   if not nb.metadata.get('papermill'):
      nb.metadata.papermill = {}
   nb.metadata.papermill['parameters'] = params
      

def find_first_tagged_cell_index(nb, tag):
   parameters_indices = []
   for idx, cell in enumerate(nb.cells):
      if tag in cell.get('metadata', {}).get('tags', {}):
         parameters_indices.append(idx)
   if not parameters_indices:
      return -1
   return parameters_indices[0]



# exception to indicate the kernel needs restarting
class RestartKernel(Exception):
   pass


class ExecuteHandler(StreamRequestHandler):

   def handle(self):

      try:
         # read options
         input = str(self.rfile.readline().strip(), 'utf-8')
         options = json.loads(input)

         # stream status back to client
         def status(msg):
            self.message("status", msg)
      
         # execute the notebook
         persist = notebook_execute(options, status)
         if not persist:
            self.server.request_exit()
      except RestartKernel:
         self.message("restart")
         self.server.request_exit()
      except Exception as e:
         self.message("error", str(e))
         self.server.request_exit()

   # write a message back to the client      
   def message(self, type, data = ""):
      message = {
         "type": type,
         "data": data 
      }
      self.wfile.write(bytearray(json.dumps(message) + "\n", 'utf-8'))
      self.wfile.flush()
  

class ExecuteServer(TCPServer):

   allow_reuse_address = True
   exit_pending = False

   def __init__(self, port, timeout):
      self.timeout = timeout
      super().__init__(("localhost",port), ExecuteHandler)

   def handle_request(self):
      if self.exit_pending:
         self.exit()
      super().handle_request()

   def handle_timeout(self):
      self.exit()

   def request_exit(self):
      self.exit_pending = True

   def exit(self):
      self.server_close()
      sys.exit(0)

  
def run_server(options):
   try:
      with ExecuteServer(options["port"], options["timeout"]) as server:  
         while True:
            server.handle_request() 
   except Exception as e:
      logger.exception(e)

def run_server_daemon(options):
   try:
      with daemon.DaemonContext(working_directory = os.getcwd()):
         run_server(options)
   except Exception as e:
      logger.exception(e)


if __name__ == "__main__":

   # setup logging
   logger = logging.getLogger(__name__)  
   logger.setLevel(logging.WARNING)
   stderr_handler = logging.StreamHandler(sys.stderr)
   logger.addHandler(stderr_handler)
   file_handler = logging.FileHandler('quarto-jupyter.log')
   logger.addHandler(file_handler)

   # debug mode server
   if "--serve" in sys.argv:

      run_server({
         "port": 5555,
         "timeout": 3000
      })
      
   else:

      input = json.load(sys.stdin)

      if input["command"] == "start":
         
         run_server_daemon(input["options"])

      elif input["command"] == "execute":

         def status(msg):
            sys.stderr.write(msg)
            sys.stderr.flush()
          
         notebook_execute(input["options"], status)


