/*
    Author: Qinbo Li
    Date: 10/17/2017
    Requirement: jquery-3.2.1
    This file defines the behavior of the choice-panel
*/

const csrfToken = window.CSRF_TOKEN || "";

$(document).ready(function(){
    $(document).on("click", "li.input_radio", function(e) {
        e.preventDefault();
        e.stopPropagation();
        $(this).parent().find("li.input_radio").removeClass("ion-android-radio-button-on");
        $(this).parent().find("li.input_radio").addClass("ion-android-radio-button-off");
        $(this).removeClass("ion-android-radio-button-off");
        $(this).addClass("ion-android-radio-button-on");
        var $selected_id = $(this).attr("id");
        var $diff = $(this).parent().parent().find("li.diff");
        var $same = $(this).parent().parent().find("li.same");
        if($selected_id.indexOf("a1") > 0 || $selected_id.indexOf("a2") > 0 || $selected_id.indexOf("a3") > 0) {
            $diff.css("border-color", "#30819c");
            $same.css("border-color", "transparent");
        }
        else {
            $diff.css("border-color", "transparent");
            $same.css("border-color", "#30819c");
        }

        fetch('/update_selection', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken
            },
            body: JSON.stringify({'id': $selected_id})

        })
        .then(response => {
            if (!response.ok) {
                throw new Error("fff");
            }
            return response.json();
        })
    });
})


$(document).ready(function() {
    function getUnansweredPairIndexes() {
        const unanswered = [];
        $(".choice-panel").each(function(index) {
            const hasSelected = $(this).find("li.input_radio.ion-android-radio-button-on").length > 0;
            if (!hasSelected) {
                unanswered.push(index + 1);
            }
        });
        return unanswered;
    }

    $(document).on("click", "button, .submit-button", function(e) {
        var $button = $(this);
        var buttonText = ($button.text() || "").trim().toLowerCase();
        var isSelectionSubmit =
            $button.hasClass("submit-button") ||
            $button.attr("id") === "submit-selections" ||
            buttonText === "submit";

        if (!isSelectionSubmit) {
            return;
        }

        e.preventDefault();

        const unansweredPairs = getUnansweredPairIndexes();
        if (unansweredPairs.length > 0) {
            alert(
                "Please answer all pairs before submitting. Missing responses for pair(s): " +
                unansweredPairs.join(", ")
            );
            return;
        }

        fetch('/submit_selections', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'X-CSRF-Token': csrfToken
            }
        })
        .then(response => {
            if (!response.ok) {
                return response.json().then(payload => {
                    const message = (payload && payload.error) ? payload.error : "Failed to submit selections";
                    throw new Error(message);
                }).catch(() => {
                    throw new Error("Failed to submit selections");
                });
            }
            return response.json();
        })
        .then(() => {
            alert("Thank you for participating! Your submissions have been recorded. " +
                "If you would like to change your submissions, you may do so and then resubmit anytime.");
        })
        .catch(() => {
            alert("Submission failed. Please make sure all responses are filled and try again.");
        });
    });
});