(() => {
    "use strict";

    const form = document.querySelector("#evidence-upload-form");
    const fileInput = document.querySelector("#evidence-file");
    const filenameInput = document.querySelector("#display-filename");
    const progress = document.querySelector("#upload-progress");
    let transferActive = false;

    const setProgress = (message) => {
        if (progress) progress.textContent = message;
    };

    const csrfToken = () => form.querySelector("input[name=csrfmiddlewaretoken]").value;

    const pollStatus = async (url) => {
        const response = await fetch(url, {credentials: "same-origin", headers: {Accept: "application/json"}});
        if (!response.ok) return;
        const status = await response.json();
        setProgress(status.message);
        if (!["available", "rejected", "processing_failure"].includes(status.state)) {
            window.setTimeout(() => pollStatus(url), 3000);
        } else {
            window.setTimeout(() => window.location.reload(), 500);
        }
    };

    if (form && fileInput && filenameInput) {
        fileInput.addEventListener("change", () => {
            if (fileInput.files.length) filenameInput.value = fileInput.files[0].name;
        });

        form.addEventListener("submit", async (event) => {
            const file = fileInput.files[0];
            if (!file) return;
            event.preventDefault();
            setProgress("Preparing secure upload…");

            const initiation = new URLSearchParams({
                assessment_id: form.elements.assessment_id.value,
                display_filename: filenameInput.value,
                declared_size: String(file.size),
                content_type: file.type,
                enhanced: "1",
                csrfmiddlewaretoken: csrfToken(),
            });
            let response;
            try {
                response = await fetch(form.action, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {"Content-Type": "application/x-www-form-urlencoded", Accept: "application/json"},
                    body: initiation,
                });
            } catch (_error) {
                setProgress("The upload could not be prepared. Try again.");
                return;
            }
            const signed = await response.json();
            if (!response.ok) {
                setProgress(signed.error || "The upload could not be prepared.");
                return;
            }

            const body = new FormData();
            Object.entries(signed.fields).forEach(([name, value]) => body.append(name, value));
            body.append("file", file);
            const request = new XMLHttpRequest();
            transferActive = true;
            request.upload.addEventListener("progress", (uploadEvent) => {
                if (uploadEvent.lengthComputable) {
                    setProgress(`Uploading: ${Math.round((uploadEvent.loaded / uploadEvent.total) * 100)}%`);
                }
            });
            request.addEventListener("load", async () => {
                transferActive = false;
                if (request.status < 200 || request.status >= 300) {
                    setProgress("S3 rejected the upload. Check the file and try again.");
                    return;
                }
                setProgress("Uploaded. Security checks are in progress.");
                await fetch(signed.received_url, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {"X-CSRFToken": csrfToken()},
                });
                pollStatus(signed.status_url);
            });
            request.addEventListener("error", () => {
                transferActive = false;
                setProgress("The transfer was interrupted. Return later to check whether S3 received it.");
            });
            request.open("POST", signed.upload_url);
            request.send(body);
        });

        window.addEventListener("beforeunload", (event) => {
            if (transferActive) {
                event.preventDefault();
                event.returnValue = "";
            }
        });
    }

    document.querySelectorAll("tr[data-status-url]").forEach((row) => {
        if (["available", "rejected"].includes(row.dataset.state)) return;
        const refresh = async () => {
            const response = await fetch(row.dataset.statusUrl, {credentials: "same-origin"});
            if (!response.ok) return;
            const status = await response.json();
            row.dataset.state = status.state;
            row.querySelector(".evidence-status").textContent = status.label;
            const action = row.querySelector(".evidence-action");
            action.textContent = "";
            if (status.download_url) {
                const link = document.createElement("a");
                link.href = status.download_url;
                link.textContent = "Download";
                action.appendChild(link);
            } else if (!["rejected", "processing_failure"].includes(status.state)) {
                window.setTimeout(refresh, 3000);
            }
        };
        window.setTimeout(refresh, 1000);
    });
})();
