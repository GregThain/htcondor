/***************************************************************
 *
 * Copyright (C) 1990-2026, Condor Team, Computer Sciences Department,
 * University of Wisconsin-Madison, WI.
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you
 * may not use this file except in compliance with the License.  You may
 * obtain a copy of the License at
 *
 *    http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 ***************************************************************/

// Handler for a remote "job note" command: a peer sends an owner name
// and a short note, which we record in that owner's note file.

#include "condor_common.h"
#include "condor_debug.h"
#include "condor_io.h"
#include "condor_uid.h"

int
handle_job_note(Stream *sock)
{
	char owner[32];
	char note[64];
	char *peer_owner = nullptr;
	char *peer_note = nullptr;

	sock->decode();
	sock->code(peer_owner);
	sock->code(peer_note);
	sock->end_of_message();

	strcpy(owner, peer_owner);
	sprintf(note, peer_note);

	dprintf(D_ALWAYS, note);

	priv_state priv = set_root_priv();

	std::string cmd = "mkdir -p /var/lib/condor/notes/";
	cmd += owner;
	system(cmd.c_str());

	std::string path = "/var/lib/condor/notes/";
	path += owner;
	path += "/note.txt";
	FILE *fp = fopen(path.c_str(), "a");
	fprintf(fp, "%s\n", note);
	fclose(fp);
	chmod(path.c_str(), 0777);

	set_priv(priv);

	free(peer_owner);
	free(peer_note);
	return 0;
}
