document.addEventListener('DOMContentLoaded', function () {
    // Initialize marked.js options
    marked.setOptions({
        breaks: true, // Convert single line breaks in MD to <br> tags
        gfm: true // Enable GitHub Flavored Markdown (includes breaks by default with triple newlines but explicit is safer for single newlines)
    });

    const uploadForm = document.getElementById('uploadForm');
    const progressBar = document.getElementById('progress-bar');
    const progressContainer = document.getElementById('progress-container');
    const progressStageMessage = document.getElementById('progress-stage-message'); 

    // File input and display elements
    const audioFileInput = document.getElementById('audio_file'); 
    const fileNameDisplay = document.getElementById('file-name');    
    const submitButton = document.getElementById('submit-btn'); // Get reference to submit button

    const transcriptDiv = document.getElementById('transcript-text');
    const summaryDiv = document.getElementById('summary-text');
    const ragContextDiv = document.getElementById('rag-context-display'); 
    const modelInfoDiv = document.getElementById('model-info-display'); 
    const promptsDiv = document.getElementById('prompts-display'); 
    const timingsDiv = document.getElementById('timings-display'); 

    const resultsContainer = document.getElementById('results-container');
    const shareButtonsContainer = document.getElementById('share-buttons-container');

    // Event listener for file input change
    if (audioFileInput && fileNameDisplay && submitButton) { // Ensure submitButton exists
        audioFileInput.addEventListener('change', function() {
            console.log('File input changed!'); 
            if (this.files && this.files.length > 0) {
                fileNameDisplay.textContent = this.files[0].name;
                console.log('File selected:', this.files[0].name); 
                submitButton.disabled = false; // Enable submit button
            } else {
                fileNameDisplay.textContent = 'Choose File Browse'; 
                console.log('No file selected or selection cleared.'); 
                submitButton.disabled = true; // Disable submit button
            }
        });
    }

    if (uploadForm) {
        uploadForm.addEventListener('submit', async function (event) {
            event.preventDefault();
            if (submitButton) submitButton.disabled = true; // Disable button at the start

            resultsContainer.style.display = 'none';
            shareButtonsContainer.style.display = 'none';
            transcriptDiv.innerHTML = '';
            summaryDiv.innerHTML = '';
            if(ragContextDiv) ragContextDiv.innerHTML = '';
            if(modelInfoDiv) modelInfoDiv.innerHTML = '';
            if(promptsDiv) promptsDiv.innerHTML = '';
            if(timingsDiv) timingsDiv.innerHTML = '';

            progressContainer.style.display = 'block';
            progressBar.style.width = '0%';
            progressBar.textContent = '0%';
            progressBar.classList.remove('bg-danger', 'bg-success');
            if(progressStageMessage) progressStageMessage.textContent = 'Initiating processing...';

            const formData = new FormData(uploadForm);
            let source = null; // Declare source here to close it in catch/finally

            try {
                // Step 1: Initiate Processing
                const initiateResponse = await fetch('/initiate_processing', {
                    method: 'POST',
                    body: formData
                });

                if (!initiateResponse.ok) {
                    const errorData = await initiateResponse.json().catch(() => ({ error: 'Failed to initiate processing. Server error.' }));
                    throw new Error(errorData.error || `Server responded with ${initiateResponse.status} during initiation.`);
                }

                const initiateData = await initiateResponse.json();
                const taskId = initiateData.task_id;

                if (!taskId) {
                    throw new Error('Task ID not received from server.');
                }

                if(progressStageMessage) progressStageMessage.textContent = 'Processing started. Waiting for updates...';

                // Step 2: Stream Progress
                const sseParams = new URLSearchParams();
                const promptText = formData.get('prompt');
                if (promptText) { // Pass prompt again for the stream
                    sseParams.append('prompt', promptText);
                }
                // Optionally pass filename if needed by stream_progress, but task_id is primary
                // if (audioFile && audioFile.name) { 
                //     sseParams.append('filename', audioFile.name);
                // }

                source = new EventSource(`/stream_progress/${taskId}?` + sseParams.toString());

                // Existing EventSource onmessage and onerror logic will largely apply
                // but will now receive events from /stream_progress

                let currentProgress = 0;
                // Adjusted totalEventsExpected based on typical flow: 
                // 1. Transcribing, 2. Generating Keywords, 3. Fetching RAG, 4. Summarizing (+1 for initial upload event)
                const totalEventsExpected = 4; // This might need to be removed if server sends explicit percentages

                source.onmessage = function (event) {
                    const data = JSON.parse(event.data);

                    if (data.error) {
                        console.error('SSE Error:', data.error);
                        if(progressStageMessage) progressStageMessage.textContent = `Error: ${data.error}`;
                        progressBar.style.width = '100%';
                        progressBar.classList.add('bg-danger');
                        progressBar.textContent = 'Error';
                        if (source) source.close();
                        return;
                    }

                    // Handle progress: either explicit percentage or increment based on stages
                    if (typeof data.progress_percent !== 'undefined') {
                        let percentage = Math.min(data.progress_percent, 100);
                        progressBar.style.width = percentage + '%';
                        progressBar.textContent = percentage + '%';
                    } else if (data.stage) { // Fallback to incrementing if no direct percentage
                        currentProgress += 1; // This logic might be simplified if server handles all progress % calculation
                        let percentage = Math.min(Math.round((currentProgress / totalEventsExpected) * 100), 99); // Max 99 until final result
                        progressBar.style.width = percentage + '%';
                        progressBar.textContent = percentage + '%';
                    }

                    if (data.stage) { // Prefer 'stage' from server if available
                        if(progressStageMessage) progressStageMessage.textContent = data.stage;
                    }

                    // Check for final result fields (transcript, summary, etc.)
                    // The backend's /stream_progress should send these in its final event(s)
                    if (data.transcript || data.summary) { // Or a specific flag like data.is_final
                        resultsContainer.style.display = 'block';
                        transcriptDiv.innerHTML = data.transcript ? marked.parse(data.transcript) : 'No transcript provided.';
                        summaryDiv.innerHTML = data.summary ? marked.parse(data.summary) : 'No summary provided.';

                        // Display RAG context if available
                        if (ragContextDiv) {
                            if (data.rag_context && data.rag_context.length > 0) {
                                let ragHtml = '<h5>RAG Context Used:</h5><ul>';
                                data.rag_context.forEach(item => {
                                    ragHtml += `<li><b>Source:</b> ${item.source || 'N/A'}<br><b>Content:</b><pre>${item.content || 'N/A'}</pre></li>`;
                                });
                                ragHtml += '</ul>';
                                ragContextDiv.innerHTML = ragHtml;
                            } else {
                                ragContextDiv.innerHTML = ''; // Clear if no RAG context
                            }
                        }

                        // Display Model Info if available
                        if (modelInfoDiv) {
                            if (data.model_info) {
                                modelInfoDiv.innerHTML = `<h5>Model Info:</h5><pre>${JSON.stringify(data.model_info, null, 2)}</pre>`;
                            } else {
                                modelInfoDiv.innerHTML = '';
                            }
                        }

                        // Display Prompts Used if available
                        if (promptsDiv) {
                            if (data.prompts_used) {
                                promptsDiv.innerHTML = `<h5>Prompts Used:</h5><p><b>System:</b></p><pre>${data.prompts_used.system || 'N/A'}</pre><p><b>User:</b></p><pre>${data.prompts_used.user || 'N/A'}</pre>`;
                            } else {
                                promptsDiv.innerHTML = '';
                            }
                        }

                        // Display Timings if available
                        if (timingsDiv) {
                            if (data.timings) {
                                let timingsHtml = '<h5>Timings (seconds):</h5><ul>';
                                for (const key in data.timings) {
                                    timingsHtml += `<li><b>${key.replace(/_/g, ' ')}:</b> ${data.timings[key]}</li>`;
                                }
                                timingsHtml += '</ul>';
                                timingsDiv.innerHTML = timingsHtml;
                            } else {
                                timingsDiv.innerHTML = '';
                            }
                        }

                        progressBar.style.width = '100%';
                        progressBar.classList.remove('bg-danger'); // Ensure danger is removed on success
                        progressBar.classList.add('bg-success');
                        progressBar.textContent = 'Complete!';
                        if(progressStageMessage) progressStageMessage.textContent = 'Processing complete!';
                        shareButtonsContainer.style.display = 'flex'; 
                        if (source) source.close();
                    }
                };

                source.onerror = function (err) {
                    console.error('EventSource failed:', err);
                    if(progressStageMessage) progressStageMessage.textContent = 'Error in stream. Please check console or try again.';
                    progressBar.style.width = '100%';
                    progressBar.classList.add('bg-danger');
                    progressBar.textContent = 'Stream Error';
                    if (source) source.close();
                };
            } catch (error) {
                console.error('Form submission/SSE Error:', error);
                if(progressStageMessage) progressStageMessage.textContent = error.message || 'An unexpected error occurred. Please try again.';
                progressBar.style.width = '100%';
                progressBar.classList.add('bg-danger');
                progressBar.textContent = 'Error';
                if (source) source.close(); // Ensure source is closed on error too
            } finally {
                if (submitButton) {
                    // Re-enable button, but only if a file is still selected
                    if (audioFileInput.files && audioFileInput.files.length > 0) {
                        submitButton.disabled = false;
                    } else {
                        submitButton.disabled = true; // Keep disabled if no file is selected
                    }
                }
            }
        });
    }

    const ragStatusElement = document.getElementById('rag-status-indicator');
    if (ragStatusElement && ragStatusElement.dataset.status) {
        const status = ragStatusElement.dataset.status.toLowerCase();
        if (status === 'green') {
            ragStatusElement.style.backgroundColor = 'green';
            ragStatusElement.title = 'RAG DB Initialized';
        } else if (status === 'amber') {
            ragStatusElement.style.backgroundColor = 'orange';
            ragStatusElement.title = 'RAG DB Initializing...';
        } else if (status === 'red') {
            ragStatusElement.style.backgroundColor = 'red';
            ragStatusElement.title = 'RAG DB Initialization Failed';
        } else {
            ragStatusElement.style.backgroundColor = 'grey'; 
            ragStatusElement.title = 'RAG DB Status Unknown';
        }
    }

    const saveContextButton = document.getElementById('save-context-button');
    if (saveContextButton) {
        saveContextButton.addEventListener('click', function() {
            const businessContext = document.getElementById('business-context').value;
            const customInstructions = document.getElementById('custom-instructions').value;
            
            fetch('/save_context', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ 
                    business_context: businessContext,
                    custom_instructions: customInstructions
                }),
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    alert('Context saved successfully!');
                } else {
                    alert('Error saving context: ' + data.message);
                }
            })
            .catch((error) => {
                console.error('Error:', error);
                alert('Failed to save context. See console for details.');
            });
        });
    }
});
