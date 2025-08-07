/*
    Author: Qinbo Li
    Date: 12/22/2017
    Requirement: jquery-3.2.1
    This file is for form submit, and sending user click data
*/

function post(path, params, method) {
    // COPYRIGHT: https://stackoverflow.com/questions/133925/javascript-post-request-like-a-form-submit
    method = method || "post"; // Set method to post by default if not specified.
    var formData = new FormData();
    formData.append("user_data", params);
    var request = new XMLHttpRequest();
    request.open("POST", path);
    request.send(formData);
}

function make_cell_clickable() {
    // mark the double missing cell as unclickable
    $('.clickable_cell').each(function() {
        if( this.children[0].innerHTML.indexOf('missing') != -1 && this.children[2].innerHTML.indexOf('missing') != -1 ) {
            this.classList.remove("clickable_cell");
        }
    });

    // bind the clickable cell to ajax openning cell action
    $('.clickable_cell').bind('click', function() {
        var current_cell = this;
        if(current_cell.getAttribute("mode") != "full") {
            get_cell_ajax(current_cell);
        }
        return false;
    });

    // big cell is the name swap cell
    $('.clickable_big_cell').bind('click', function() {
        var first_name_cell = this.children[0];
        var last_name_cell = this.children[2];
        if(first_name_cell.getAttribute("mode") != "full") {
            get_cell_ajax(first_name_cell);
        }
        if(last_name_cell.getAttribute("mode") != "full") {
            get_cell_ajax(last_name_cell);
        }
        this.classList.remove("clickable_big_cell");
        return false;
    });
}

// this function is just a mirror from clickable.js
function refresh_delta() {
    $('.clickable_cell').hover(function() {
        var id1 = this.children[0].getAttribute("id");
        var d = $DELTA[id1];
        var bar_style = 'width:' + d + '%';
        $("#privacy-risk-delta").attr("style", bar_style);
        $("#privacy-risk-delta-value").html(" + " + d + "%");

        var cd = $DELTA_CDP[id1]
        var bar_style_cdp = 'width:' + cd + '%';
        $("#character-percentage-delta").attr("style", bar_style_cdp);
        $("#character-percentage-delta-value").html(" + " + cd + "%");
    }, function() {
        $("#privacy-risk-delta").attr("style", 'width: 0%');
        $("#privacy-risk-delta-value").html(" ")
        $("#character-percentage-delta").attr("style", 'width: 0%');
        $("#character-percentage-delta-value").html(" ");
    });
}

function reset_choice_panel() {
    var $options = $("li.input_radio");

    $options.click(function(e){
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

        // save the user click data
        $this_click = "user click: " + $selected_id;
        var dt = new Date();
        $click_time = "click time: " + dt.getHours() + "h" + dt.getMinutes() + "m" + dt.getSeconds() + "s";
        $click_timestamp = "click timestamp: " + dt.getTime();
        $data = [$this_click, $click_time, $click_timestamp].join()
        $user_data += $data + ";";
    });
}

/* 
    This function defines the behavior of the next button 
    1. post the user click data to server.
    2. relocate to the next page.
*/
$(function() {
    $('#button_next').bind('click', function() {
        post($SCRIPT_ROOT+'/save_data', $user_data, "post");
        window.location.href = $NEXT_URL;
    });
});

/*
    This function defines the behavior of the next button in record linkage page:
    1. disable this button
    2. post the user data to server
    3. use ajax to load the next page of data (if any)
    4. if the next page is the last page, change this button to normal next button
    5. enable the button
*/
$(function() {
    $('#button_next_rl').bind('click', function() {
        $('#button_next_rl').attr("disabled", "disabled");
        post($SCRIPT_ROOT+'/save_data', $user_data, "post");
        $.ajax({
            url: $SCRIPT_ROOT + $THIS_URL + '/next',
            data: {},
            error: function() {},
            dataType: 'json',
            success: function(data) {
                $DELTA = data['delta'];
                $DELTA_CDP = data['delta_cdp'];
                $("#table_content").html(data['page_content']);
                make_cell_clickable();
                refresh_delta();
                reset_choice_panel();
                $('#button_next_rl').css("display", "none");
                $('#button_next').css("display", "inline");
            },
            type: 'GET'
        });
        return false;
    });
});
